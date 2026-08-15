from wtforms import ValidationError, Form, StringField, TextAreaField, SelectField, HiddenField, FileField, BooleanField
import logging
import time
from typing import Any

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route, Mount
from sqlalchemy import text

from markupsafe import Markup
from wtforms import SelectField

from bot.misc import EnvKeys
from bot.database.methods.audit import log_audit

logger = logging.getLogger(__name__)


class LoginRateLimiter:
    """In-memory rate limiter for login attempts by IP."""

    def __init__(self, max_attempts: int = 5, lockout_seconds: int = 900):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._attempts: dict[str, list[float]] = {}
        self._last_cleanup: float = time.time()

    def is_blocked(self, ip: str) -> bool:
        if ip not in self._attempts:
            return False
        now = time.time()
        self._attempts[ip] = [t for t in self._attempts[ip] if now - t < self.lockout_seconds]
        return len(self._attempts[ip]) >= self.max_attempts

    def record_failure(self, ip: str) -> None:
        now = time.time()
        if now - self._last_cleanup > 600:
            self._attempts = {
                k: [t for t in v if now - t < self.lockout_seconds]
                for k, v in self._attempts.items()
                if any(now - t < self.lockout_seconds for t in v)
            }
            self._last_cleanup = now
        if ip not in self._attempts:
            self._attempts[ip] = []
        self._attempts[ip].append(now)

    def reset(self, ip: str) -> None:
        self._attempts.pop(ip, None)


_login_limiter = LoginRateLimiter()
from bot.database.main import Database
from bot.database.models import User, Role, Categories, Goods, ItemValues, BoughtGoods, Operations, Payments
from bot.database.models.main import (
    StoreSettings, MainMenuButtonSettings, ReferralEarnings, AuditLog,
    PromoCodes, PromoCodeUsages, CartItems, Reviews,
    ProductCustomerField, ProductRestockSubscription, Order, OrderItem,
    CheckoutIntakeDraft, OrderCustomerInput, ManualFulfillmentJob
)
from bot.misc.metrics import get_metrics
from bot.misc.caching import get_cache_manager


# Authentication
class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        ip = request.client.host

        if _login_limiter.is_blocked(ip):
            await log_audit("web_login_blocked", level="WARNING", details=f"ip={ip}", ip_address=ip)
            return False

        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        if username == EnvKeys.ADMIN_USERNAME and password == EnvKeys.ADMIN_PASSWORD:
            if (
                username == "admin" and password == "admin"
                and ip not in ("127.0.0.1", "::1", "localhost")
            ):
                await log_audit("web_login_blocked_default_creds", level="WARNING", details=f"ip={ip}", ip_address=ip)
                return False
            request.session.update({"authenticated": True})
            _login_limiter.reset(ip)
            await log_audit("web_login", user_id=None, details=f"user={username}", ip_address=ip)
            return True

        _login_limiter.record_failure(ip)
        await log_audit("web_login_failed", level="WARNING", details=f"user={username}", ip_address=ip)
        return False

    async def logout(self, request: Request) -> bool:
        await log_audit("web_logout", ip_address=request.client.host)
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        auth_val = request.session.get("authenticated", False)
        print("AUTHENTICATE CALLED! Returning:", auth_val)
        return auth_val


def _safe_model_repr(model: Any, max_len: int = 500) -> str:
    """Return a truncated repr that excludes sensitive fields."""
    _sensitive = {"balance", "password", "secret", "token", "value"}
    parts = []
    for col in getattr(model, "__table__", None).columns if hasattr(model, "__table__") else ():
        if col.name in _sensitive:
            continue
        if col.name in model.__dict__:
            val = getattr(model, col.name, None)
            parts.append(f"{col.name}={val!r}")
    result = f"{type(model).__name__}({', '.join(parts)})"
    return result[:max_len]


# Audited base view for mutable models
class AuditModelView(ModelView):
    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        try:
            action = f"sqladmin_{'create' if is_created else 'update'}"
            await log_audit(
                action,
                resource_type=self.name,
                resource_id=str(getattr(model, 'id', getattr(model, 'name', None))),
                details=_safe_model_repr(model),
                ip_address=request.client.host,
            )
        except Exception as e:
            import traceback
            open('traceback.txt', 'a').write('\n\n--- after_model_change ---\n' + traceback.format_exc())
            raise e

    async def after_model_delete(self, model: Any, request: Request) -> None:
        await log_audit(
            "sqladmin_delete",
            resource_type=self.name,
            resource_id=str(getattr(model, 'id', getattr(model, 'name', None))),
            details=_safe_model_repr(model),
            ip_address=request.client.host,
        )


# Model Views
class UserAdmin(AuditModelView, model=User):
    column_list = [User.telegram_id, User.telegram_username, User.first_name, User.last_name, User.balance, User.role_id, User.registration_date, User.is_blocked]
    column_details_list = [
        User.telegram_id, User.telegram_username, User.first_name, User.last_name,
        User.profile_updated_at, User.role_id, User.balance, User.referral_id,
        User.registration_date, User.is_blocked
    ]
    column_searchable_list = [User.telegram_id, User.telegram_username, User.first_name, User.last_name]
    column_sortable_list = [User.telegram_id, User.balance, User.registration_date]
    column_default_sort = (User.registration_date, True)

    form_columns = [
        User.balance,
        User.is_blocked,
        "role",
        User.referral_id,
    ]

    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"

    async def get_object_for_edit(self, value: Any) -> Any:
        from sqlalchemy import select
        from sqlalchemy.orm import noload, joinedload
        try:
            pk = int(value)
        except (ValueError, TypeError):
            return None

        stmt = (
            select(User)
            .where(User.telegram_id == pk)
            .options(noload("*"))
            .options(joinedload(User.role))
        )
        rows = await self._run_query(stmt)
        return rows[0] if rows else None


_PERM_FLAGS = [
    (1,   "USE"),
    (2,   "BROADCAST"),
    (4,   "SETTINGS"),
    (8,   "USERS"),
    (16,  "CATALOG"),
    (32,  "ADMINS"),
    (64,  "OWNER"),
    (128, "STATS"),
    (256, "BALANCE"),
    (512, "PROMOS"),
]


def _format_perms_html(model, name):
    perms = getattr(model, name, 0) or 0
    if not perms:
        return Markup('<span style="color:#999">\u2014</span>')
    badges = []
    for bit, label in _PERM_FLAGS:
        if perms & bit:
            badges.append(
                f'<span style="display:inline-block;background:#e2e8f0;padding:1px 6px;'
                f'border-radius:4px;margin:1px;font-size:12px">{label}</span>'
            )
    raw = f'<span style="color:#999;font-size:11px;margin-left:4px">({perms})</span>'
    return Markup(" ".join(badges) + raw)


class RoleAdmin(AuditModelView, model=Role):
    column_list = [Role.id, Role.name, Role.default, Role.permissions]
    column_details_exclude_list = ["users"]
    column_sortable_list = [Role.id, Role.name]
    name = "Role"
    name_plural = "Roles"
    icon = "fa-solid fa-shield-halved"
    column_formatters = {"permissions": _format_perms_html}
    column_formatters_detail = {"permissions": _format_perms_html}
    form_args = {
        "permissions": {
            "description": (
                "Bitmask value — sum the flags you need: "
                "USE=1, BROADCAST=2, SETTINGS=4, USERS=8, CATALOG=16, ADMINS=32, "
                "OWNER=64, STATS=128, BALANCE=256, PROMOS=512. "
                "Example: 927 = full Admin, 1023 = all (Owner)."
            ),
        },
    }


async def normalize_parent_id(raw_val: Any, current_model_id: int | None = None) -> int | None:
    if raw_val == "" or raw_val is None:
        return None

    from bot.database.models.main import Categories
    if isinstance(raw_val, Categories):
        val = raw_val.id
    else:
        try:
            val = int(raw_val)
        except (ValueError, TypeError):
            raise ValueError("Parent ID must be an integer or valid numeric string.")

    if val < 0:
        raise ValueError("Parent ID cannot be negative.")

    if current_model_id is not None and val == current_model_id:
        raise ValueError("Cannot assign a category as its own parent.")

    from bot.database.main import Database
    from sqlalchemy import select

    async with Database().session() as s:
        parent_obj = (await s.execute(select(Categories).where(Categories.id == val))).scalars().first()
        if not parent_obj:
            raise ValueError("The selected Parent category does not exist.")

        if current_model_id is not None:
            curr = parent_obj.parent_id
            while curr is not None:
                if curr == current_model_id:
                    raise ValueError("Circular dependency detected in parent category assignment.")
                curr = (await s.execute(select(Categories.parent_id).where(Categories.id == curr))).scalar()

    return val


class CategoryBaseForm(Form):
    image_upload = FileField("Category Image (Upload new)", render_kw={"class": "form-control", "accept": "image/*"})
    remove_image = BooleanField("Remove existing image", render_kw={"class": "form-check-input"})
    remove_image = BooleanField("Remove existing image", render_kw={"class": "form-check-input"})

class CategoryAdmin(AuditModelView, model=Categories):
    column_list = [Categories.id, Categories.name, Categories.description, Categories.parent_id, Categories.display_order]
    column_searchable_list = [Categories.name]
    column_sortable_list = [Categories.id, Categories.name, Categories.display_order]
    name = "Category"
    name_plural = "Categories"
    icon = "fa-solid fa-folder"

    column_details_list = [
        Categories.id,
        Categories.name,
        Categories.name_en, Categories.name_ar, Categories.name_ru, Categories.name_zh, Categories.name_vi, Categories.name_tr, Categories.name_es,
        Categories.description,
        Categories.description_en, Categories.description_ar, Categories.description_ru, Categories.description_zh, Categories.description_vi, Categories.description_tr, Categories.description_es,
        Categories.parent_id,
        Categories.image_path,
        Categories.children_buttons_per_row,
        Categories.display_order,
    ]
    column_labels = {
        Categories.name_en: "Name (English)",
        Categories.name_ar: "Name (Arabic)",
        Categories.description_en: "Description (English)",
        Categories.description_ar: "Description (Arabic)",
    }
    form_columns = [
        Categories.name,
        Categories.name_en, Categories.name_ar, Categories.name_ru, Categories.name_zh, Categories.name_vi, Categories.name_tr, Categories.name_es,
        Categories.description,
        Categories.description_en, Categories.description_ar, Categories.description_ru, Categories.description_zh, Categories.description_vi, Categories.description_tr, Categories.description_es,
        Categories.parent,
        Categories.children_buttons_per_row,
        Categories.display_order,
    ]

    form_base_class = CategoryBaseForm
    edit_template = "admin/category_edit.html"

    form_overrides = {
        "children_buttons_per_row": SelectField
    }
    form_args = {
        "children_buttons_per_row": {
            "choices": [(1, "1 button per row"), (2, "2 buttons per row")],
            "coerce": int,
            "description": "Controls only direct child subcategories.",
            "label": "Subcategory buttons per row",
            "default": 1
        }
    }

    async def insert_model(self, request, data: dict):
        temp_data = getattr(request.state, "temp_form_data", {})
        request.state.temp_form_data = temp_data
        model_data = dict(data)
        model_data.pop("image_upload", None)
        model_data.pop("remove_image", None)
        try:
            return await super().insert_model(request, model_data)
        except Exception as e:
            rollback = getattr(request.state, "category_image_to_rollback", None)
            if rollback:
                import os
                if os.path.isfile(rollback):
                    os.remove(rollback)
            raise e

    async def update_model(self, request, pk: str, data: dict):
        temp_data = getattr(request.state, "temp_form_data", {})
        request.state.temp_form_data = temp_data
        model_data = dict(data)
        model_data.pop("image_upload", None)
        model_data.pop("remove_image", None)
        try:
            return await super().update_model(request, pk, model_data)
        except Exception as e:
            rollback = getattr(request.state, "category_image_to_rollback", None)
            if rollback:
                import os
                if os.path.isfile(rollback):
                    os.remove(rollback)
            raise e

    async def on_model_change(self, data, model, is_created, request):
        import os
        from bot.misc.env import EnvKeys
        from bot.database.main import Database
        from bot.database.methods.create import get_next_display_order

        # 1. Normalize and set parent_id securely
        raw_parent = data.pop("parent", None)
        if raw_parent is None and "parent_id" in data:
            raw_parent = data.pop("parent_id")

        current_id = getattr(model, "id", None)
        old_parent_id = getattr(model, "parent_id", None)
        new_parent_id = await normalize_parent_id(raw_parent, current_id)

        # Directly update the model so SQLAdmin doesn't crash on string -> object assignment
        model.parent_id = new_parent_id

        # 2. Handle auto-append for display_order
        user_provided_order = data.get("display_order")
        old_display_order = getattr(model, "display_order", None)

        if is_created and user_provided_order is None:
            async with Database().session() as s:
                data["display_order"] = await get_next_display_order(new_parent_id, s)
        elif not is_created and old_parent_id != new_parent_id:
            if user_provided_order == old_display_order or user_provided_order is None:
                async with Database().session() as s:
                    data["display_order"] = await get_next_display_order(new_parent_id, s)

        # 3. Handle image uploads
        temp_data = getattr(request.state, "temp_form_data", {})
        image_upload = temp_data.get("image_upload")
        if image_upload is None:
            image_upload = data.pop("image_upload", None)
        remove_image = temp_data.get("remove_image", False)
        if not remove_image:
            remove_image = data.pop("remove_image", False)

        old_image_path = getattr(model, "image_path", None)
        request.state.category_image_to_delete = None
        request.state.category_image_to_rollback = None
        base_dir = os.path.abspath(EnvKeys.CATEGORY_IMAGES_ROOT)

        relative_path, absolute_path = await handle_managed_image_upload(
            image_upload, base_dir, "category_images"
        )

        if relative_path:
            model.image_path = relative_path
            if old_image_path:
                request.state.category_image_to_delete = old_image_path
            request.state.category_image_to_rollback = absolute_path
        elif remove_image:
            model.image_path = None
            if old_image_path:
                request.state.category_image_to_delete = old_image_path

        if getattr(super(), "on_model_change", None):
            await super().on_model_change(data, model, is_created, request)

    async def after_model_change(self, data, model, is_created, request):
        old_image = getattr(request.state, "category_image_to_delete", None)
        if old_image:
            from bot.misc.utils import resolve_category_image_path
            cleanup_orphaned_image(old_image, resolve_category_image_path)
        if getattr(super(), "after_model_change", None):
            await super().after_model_change(data, model, is_created, request)

    async def after_model_delete(self, model, request):
        image_path = getattr(model, 'image_path', None)
        if image_path:
            from bot.misc.utils import resolve_category_image_path
            cleanup_orphaned_image(image_path, resolve_category_image_path)
        if getattr(super(), "after_model_delete", None):
            await super().after_model_delete(model, request)


class StoreSettingsAdmin(AuditModelView, model=StoreSettings):
    column_list = [StoreSettings.id, StoreSettings.shop_root_title, StoreSettings.main_menu_title]
    name = "Store Setting"
    name_plural = "Store Settings"
    icon = "fa-solid fa-gear"
    can_create = False
    can_delete = False

    column_details_list = [
        StoreSettings.id,
        StoreSettings.shop_root_title,
        StoreSettings.shop_root_title_en, StoreSettings.shop_root_title_ar, StoreSettings.shop_root_title_ru, StoreSettings.shop_root_title_zh, StoreSettings.shop_root_title_vi, StoreSettings.shop_root_title_tr, StoreSettings.shop_root_title_es,
        StoreSettings.shop_root_description,
        StoreSettings.shop_root_description_en, StoreSettings.shop_root_description_ar, StoreSettings.shop_root_description_ru, StoreSettings.shop_root_description_zh, StoreSettings.shop_root_description_vi, StoreSettings.shop_root_description_tr, StoreSettings.shop_root_description_es,
        StoreSettings.main_menu_title,
        StoreSettings.main_menu_title_en, StoreSettings.main_menu_title_ar, StoreSettings.main_menu_title_ru, StoreSettings.main_menu_title_zh, StoreSettings.main_menu_title_vi, StoreSettings.main_menu_title_tr, StoreSettings.main_menu_title_es,
        StoreSettings.main_menu_description,
        StoreSettings.main_menu_description_en, StoreSettings.main_menu_description_ar, StoreSettings.main_menu_description_ru, StoreSettings.main_menu_description_zh, StoreSettings.main_menu_description_vi, StoreSettings.main_menu_description_tr, StoreSettings.main_menu_description_es,
        StoreSettings.main_menu_image_path,
        StoreSettings.main_menu_image_url,
        StoreSettings.main_menu_footer,
        StoreSettings.main_menu_footer_en, StoreSettings.main_menu_footer_ar, StoreSettings.main_menu_footer_ru, StoreSettings.main_menu_footer_zh, StoreSettings.main_menu_footer_vi, StoreSettings.main_menu_footer_tr, StoreSettings.main_menu_footer_es,
        StoreSettings.root_category_columns,
        StoreSettings.subcategory_columns,
        StoreSettings.product_columns,
        StoreSettings.root_category_buttons_per_row,
    ]
    column_labels = {
        StoreSettings.shop_root_title_en: "Shop Root Title (English)",
        StoreSettings.shop_root_title_ar: "Shop Root Title (Arabic)",
        StoreSettings.shop_root_description_en: "Shop Root Description (English)",
        StoreSettings.shop_root_description_ar: "Shop Root Description (Arabic)",
        StoreSettings.main_menu_title_en: "Main Menu Title (English)",
        StoreSettings.main_menu_title_ar: "Main Menu Title (Arabic)",
        StoreSettings.main_menu_description_en: "Main Menu Description (English)",
        StoreSettings.main_menu_description_ar: "Main Menu Description (Arabic)",
        StoreSettings.main_menu_footer_en: "Main Menu Footer (English)",
        StoreSettings.main_menu_footer_ar: "Main Menu Footer (Arabic)",
    }
    form_columns = [
        StoreSettings.shop_root_title,
        StoreSettings.shop_root_title_en, StoreSettings.shop_root_title_ar, StoreSettings.shop_root_title_ru, StoreSettings.shop_root_title_zh, StoreSettings.shop_root_title_vi, StoreSettings.shop_root_title_tr, StoreSettings.shop_root_title_es,
        StoreSettings.shop_root_description,
        StoreSettings.shop_root_description_en, StoreSettings.shop_root_description_ar, StoreSettings.shop_root_description_ru, StoreSettings.shop_root_description_zh, StoreSettings.shop_root_description_vi, StoreSettings.shop_root_description_tr, StoreSettings.shop_root_description_es,
        StoreSettings.main_menu_title,
        StoreSettings.main_menu_title_en, StoreSettings.main_menu_title_ar, StoreSettings.main_menu_title_ru, StoreSettings.main_menu_title_zh, StoreSettings.main_menu_title_vi, StoreSettings.main_menu_title_tr, StoreSettings.main_menu_title_es,
        StoreSettings.main_menu_description,
        StoreSettings.main_menu_description_en, StoreSettings.main_menu_description_ar, StoreSettings.main_menu_description_ru, StoreSettings.main_menu_description_zh, StoreSettings.main_menu_description_vi, StoreSettings.main_menu_description_tr, StoreSettings.main_menu_description_es,
        StoreSettings.main_menu_footer,
        StoreSettings.main_menu_footer_en, StoreSettings.main_menu_footer_ar, StoreSettings.main_menu_footer_ru, StoreSettings.main_menu_footer_zh, StoreSettings.main_menu_footer_vi, StoreSettings.main_menu_footer_tr, StoreSettings.main_menu_footer_es,
        StoreSettings.main_menu_image_path,
        StoreSettings.main_menu_image_url,
        StoreSettings.root_category_buttons_per_row,
        StoreSettings.root_category_columns,
        StoreSettings.subcategory_columns,
        StoreSettings.product_columns,
    ]

    from sqladmin.fields import FileField

    form_overrides = {
        "root_category_buttons_per_row": SelectField,
        "root_category_columns": SelectField,
        "subcategory_columns": SelectField,
        "product_columns": SelectField,
    }

    form_args = {
        "root_category_buttons_per_row": {
            "choices": [(1, "1 button per row"), (2, "2 buttons per row")],
            "coerce": int,
            "description": "Number of buttons per row for top-level categories.",
            "label": "Root category buttons per row",
            "default": 1
        },
        "root_category_columns": {
            "choices": [(1, "1 — One button per row"), (2, "2 — Two buttons per row")],
            "coerce": int,
            "description": "Number of buttons per row for top-level categories."
        },
        "subcategory_columns": {
            "choices": [(1, "1 — One button per row"), (2, "2 — Two buttons per row")],
            "coerce": int,
            "description": "Number of buttons per row for child/subcategories."
        },
        "product_columns": {
            "choices": [(1, "1 — One button per row"), (2, "2 — Two buttons per row")],
            "coerce": int,
            "description": "Number of buttons per row for product listings."
        }
    }


class MainMenuButtonSettingsAdmin(AuditModelView, model=MainMenuButtonSettings):
    column_list = [MainMenuButtonSettings.action_key, MainMenuButtonSettings.label_en, MainMenuButtonSettings.label_ar,
                   MainMenuButtonSettings.row_order, MainMenuButtonSettings.column_order,
                   MainMenuButtonSettings.is_enabled, MainMenuButtonSettings.owner_only]
    form_columns = [MainMenuButtonSettings.label_en, MainMenuButtonSettings.label_ar, MainMenuButtonSettings.label_ru, MainMenuButtonSettings.label_zh, MainMenuButtonSettings.label_vi, MainMenuButtonSettings.label_tr, MainMenuButtonSettings.label_es,
                    MainMenuButtonSettings.row_order, MainMenuButtonSettings.column_order,
                    MainMenuButtonSettings.is_enabled]
    can_create = False
    can_delete = False
    name = "Menu Button"
    name_plural = "Menu Buttons"
    icon = "fa-solid fa-bars"



import json
import os
import uuid
import io
from PIL import Image
from starlette.exceptions import HTTPException

async def handle_managed_image_upload(
    image_upload,
    base_dir: str,
    prefix_dir_name: str
) -> tuple[str | None, str | None]:
    """
    Validates, strips metadata, and stores an uploaded image.
    Returns (relative_path, absolute_path) if successful, or (None, None) if no file.
    """
    try:
        os.makedirs(base_dir, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create images directory: {e}")
        type_str = "Category " if "category" in prefix_dir_name else "Product "
        raise HTTPException(status_code=400, detail=f"{type_str}image storage is not writable.")

    content = None
    if image_upload and getattr(image_upload, "filename", None):
        content = await image_upload.read()

    if not content:
        return None, None

    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image exceeds 5MB size limit.")

    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
        # Reopen after verify() because verify() can leave the file pointer at EOF
        img = Image.open(io.BytesIO(content))
        # Strip EXIF and metadata by creating a new image without it
        img_data = list(img.getdata())
        img_clean = Image.new(img.mode, img.size)
        img_clean.putdata(img_data)
        format_to_save = img.format if img.format in ["JPEG", "PNG", "WEBP"] else "WEBP"
        if format_to_save == "JPEG" and img.mode in ("RGBA", "P"):
            img_clean = img_clean.convert("RGB")
        ext_map = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
        ext = ext_map.get(format_to_save, ".webp")
        new_filename = f"{uuid.uuid4()}{ext}"
        new_full_path = os.path.join(base_dir, new_filename)
    except Exception as e:
        logger.error(f"Image processing error: {e}")
        raise HTTPException(status_code=400, detail="Invalid image file uploaded.")

    try:
        img_clean.save(new_full_path, format=format_to_save)
    except OSError as e:
        logger.error(f"Failed to save image: {e}")
        type_str = "Category " if "category" in prefix_dir_name else "Product "
        raise HTTPException(status_code=400, detail=f"{type_str}image storage is not writable.")

    return f"{prefix_dir_name}/{new_filename}", new_full_path

def cleanup_orphaned_image(image_path: str, resolve_func):
    if not image_path:
        return
    try:
        full_path = resolve_func(image_path)
        if full_path:
            os.remove(full_path)
    except Exception as e:
        logger.warning(f"Failed to delete old image {image_path}: {e}")

class GoodsBaseForm(Form):
    image_upload = FileField("Product Image (Upload new)", render_kw={"class": "form-control", "accept": "image/*"})
    remove_image = BooleanField("Remove existing image", render_kw={"class": "form-check-input"})
    manual_instr_en = TextAreaField("Manual Instructions - English", render_kw={"class": "form-control"})
    manual_instr_ar = TextAreaField("Manual Instructions - Arabic", render_kw={"class": "form-control"})
    manual_instr_ru = TextAreaField("Manual Instructions - Russian", render_kw={"class": "form-control"})
    manual_instr_zh = TextAreaField("Manual Instructions - Chinese", render_kw={"class": "form-control"})
    manual_instr_vi = TextAreaField("Manual Instructions - Vietnamese", render_kw={"class": "form-control"})
    manual_instr_tr = TextAreaField("Manual Instructions - Turkish", render_kw={"class": "form-control"})
    manual_instr_es = TextAreaField("Manual Instructions - Spanish", render_kw={"class": "form-control"})

    input_intro_en = TextAreaField("Customer Input Intro - English", render_kw={"class": "form-control"})
    input_intro_ar = TextAreaField("Customer Input Intro - Arabic", render_kw={"class": "form-control"})
    input_intro_ru = TextAreaField("Customer Input Intro - Russian", render_kw={"class": "form-control"})
    input_intro_zh = TextAreaField("Customer Input Intro - Chinese", render_kw={"class": "form-control"})
    input_intro_vi = TextAreaField("Customer Input Intro - Vietnamese", render_kw={"class": "form-control"})
    input_intro_tr = TextAreaField("Customer Input Intro - Turkish", render_kw={"class": "form-control"})
    input_intro_es = TextAreaField("Customer Input Intro - Spanish", render_kw={"class": "form-control"})
    eta_preset = SelectField("Fulfillment ETA", choices=[
        ("", "Not specified"),
        ("60", "1 hour"),
        ("180", "3 hours"),
        ("360", "6 hours"),
        ("720", "12 hours"),
        ("1440", "24 hours"),
        ("2880", "48 hours"),
        ("custom", "Custom")
    ], validate_choice=False, render_kw={"class": "form-select", "id": "eta_preset"})
    is_enabled = BooleanField("Enabled in Shop", render_kw={"class": "form-check-input"})


    def process(self, formdata=None, obj=None, data=None, **kwargs):
        if obj and not formdata:
            if obj.manual_instructions_i18n:
                kwargs['manual_instr_en'] = obj.manual_instructions_i18n.get('en', '')
                kwargs['manual_instr_ar'] = obj.manual_instructions_i18n.get('ar', '')
                kwargs['manual_instr_ru'] = obj.manual_instructions_i18n.get('ru', '')
                kwargs['manual_instr_zh'] = obj.manual_instructions_i18n.get('zh', '')
                kwargs['manual_instr_vi'] = obj.manual_instructions_i18n.get('vi', '')
                kwargs['manual_instr_tr'] = obj.manual_instructions_i18n.get('tr', '')
                kwargs['manual_instr_es'] = obj.manual_instructions_i18n.get('es', '')
            if obj.customer_input_intro_i18n:
                kwargs['input_intro_en'] = obj.customer_input_intro_i18n.get('en', '')
                kwargs['input_intro_ar'] = obj.customer_input_intro_i18n.get('ar', '')
                kwargs['input_intro_ru'] = obj.customer_input_intro_i18n.get('ru', '')
                kwargs['input_intro_zh'] = obj.customer_input_intro_i18n.get('zh', '')
                kwargs['input_intro_vi'] = obj.customer_input_intro_i18n.get('vi', '')
                kwargs['input_intro_tr'] = obj.customer_input_intro_i18n.get('tr', '')
                kwargs['input_intro_es'] = obj.customer_input_intro_i18n.get('es', '')
            if obj.fulfillment_eta_minutes is not None:
                preset = str(obj.fulfillment_eta_minutes)
                if preset in ["60", "180", "360", "720", "1440", "2880"]:
                    kwargs['eta_preset'] = preset
                else:
                    kwargs['eta_preset'] = "custom"
        super().process(formdata, obj, data, **kwargs)

class GoodsAdmin(AuditModelView, model=Goods):
    column_list = [Goods.id, Goods.name, Goods.price, Goods.is_enabled, Goods.category_id, Goods.fulfillment_mode, Goods.is_popular_deal, Goods.popular_deal_order]
    column_searchable_list = [Goods.name]
    column_sortable_list = [Goods.id, Goods.name, Goods.price, Goods.is_enabled, Goods.fulfillment_mode, Goods.is_popular_deal, Goods.popular_deal_order]

    column_details_list = [
        Goods.id,
        Goods.name,
        Goods.name_en, Goods.name_ar, Goods.name_ru, Goods.name_zh, Goods.name_vi, Goods.name_tr, Goods.name_es,
        Goods.price,
        Goods.description,
        Goods.description_en, Goods.description_ar, Goods.description_ru, Goods.description_zh, Goods.description_vi, Goods.description_tr, Goods.description_es,
        Goods.category_id,
        Goods.fulfillment_mode,
        Goods.fulfillment_eta_minutes,
        Goods.manual_instructions_i18n,
        Goods.customer_input_intro_i18n,
        Goods.is_popular_deal,
        Goods.popular_deal_order,
        Goods.image_path,
        Goods.is_enabled,
    ]
    column_labels = {
        Goods.name_en: "Name (English)",
        Goods.name_ar: "Name (Arabic)",
        Goods.description_en: "Description (English)",
        Goods.description_ar: "Description (Arabic)",
    }
    form_columns = [
        Goods.name,
        Goods.name_en, Goods.name_ar, Goods.name_ru, Goods.name_zh, Goods.name_vi, Goods.name_tr, Goods.name_es,
        Goods.price,
        Goods.description,
        Goods.description_en, Goods.description_ar, Goods.description_ru, Goods.description_zh, Goods.description_vi, Goods.description_tr, Goods.description_es,
        Goods.category,
        Goods.is_enabled,
        Goods.fulfillment_mode,
        Goods.fulfillment_eta_minutes,
        Goods.is_popular_deal,
        Goods.popular_deal_order,
    ]

    form_base_class = GoodsBaseForm
    create_template = "admin/goods_create.html"
    edit_template = "admin/goods_edit.html"

    name = "Product"
    name_plural = "Products"
    icon = "fa-solid fa-box"

    form_overrides = {
        "fulfillment_mode": SelectField
    }
    form_args = {
        "fulfillment_mode": {
            "choices": [("instant", "Instant (Digital)"), ("manual", "Manual (Human)")]
        }
    }

    async def insert_model(self, request, data: dict):
        temp_data = getattr(request.state, "temp_form_data", {})
        temp_data.update({
            "manual_instr_en": data.get("manual_instr_en"), "manual_instr_ar": data.get("manual_instr_ar"),
            "manual_instr_ru": data.get("manual_instr_ru"), "manual_instr_zh": data.get("manual_instr_zh"),
            "manual_instr_vi": data.get("manual_instr_vi"), "manual_instr_tr": data.get("manual_instr_tr"),
            "manual_instr_es": data.get("manual_instr_es"),
            "input_intro_en": data.get("input_intro_en"), "input_intro_ar": data.get("input_intro_ar"),
            "input_intro_ru": data.get("input_intro_ru"), "input_intro_zh": data.get("input_intro_zh"),
            "input_intro_vi": data.get("input_intro_vi"), "input_intro_tr": data.get("input_intro_tr"),
            "input_intro_es": data.get("input_intro_es"),
            "eta_preset": data.get("eta_preset"),
            "is_enabled": data.get("is_enabled"),
        })

        request.state.temp_form_data = temp_data
        model_data = dict(data)
        for field in ["manual_instr_en", "manual_instr_ar", "manual_instr_ru", "manual_instr_zh", "manual_instr_vi", "manual_instr_tr", "manual_instr_es",
                      "input_intro_en", "input_intro_ar", "input_intro_ru", "input_intro_zh", "input_intro_vi", "input_intro_tr", "input_intro_es",
                      "eta_preset", "image_upload", "remove_image"]:
            model_data.pop(field, None)

        is_enabled = temp_data.get("is_enabled")
        if is_enabled is None:
            is_enabled = data.pop("is_enabled", None)
        if is_enabled is not None:
            model_data["is_enabled"] = is_enabled

        try:
            return await super().insert_model(request, model_data)
        except Exception as e:
            rollback = getattr(request.state, "product_image_to_rollback", None)
            if rollback:
                import os
                if os.path.isfile(rollback):
                    os.remove(rollback)
            raise e
    async def update_model(self, request, pk: str, data: dict):
        temp_data = getattr(request.state, "temp_form_data", {})
        temp_data.update({
            "manual_instr_en": data.get("manual_instr_en"), "manual_instr_ar": data.get("manual_instr_ar"),
            "manual_instr_ru": data.get("manual_instr_ru"), "manual_instr_zh": data.get("manual_instr_zh"),
            "manual_instr_vi": data.get("manual_instr_vi"), "manual_instr_tr": data.get("manual_instr_tr"),
            "manual_instr_es": data.get("manual_instr_es"),
            "input_intro_en": data.get("input_intro_en"), "input_intro_ar": data.get("input_intro_ar"),
            "input_intro_ru": data.get("input_intro_ru"), "input_intro_zh": data.get("input_intro_zh"),
            "input_intro_vi": data.get("input_intro_vi"), "input_intro_tr": data.get("input_intro_tr"),
            "input_intro_es": data.get("input_intro_es"),
            "eta_preset": data.get("eta_preset"),
            "is_enabled": data.get("is_enabled"),
        })

        request.state.temp_form_data = temp_data
        model_data = dict(data)
        for field in ["manual_instr_en", "manual_instr_ar", "manual_instr_ru", "manual_instr_zh", "manual_instr_vi", "manual_instr_tr", "manual_instr_es",
                      "input_intro_en", "input_intro_ar", "input_intro_ru", "input_intro_zh", "input_intro_vi", "input_intro_tr", "input_intro_es",
                      "eta_preset", "image_upload", "remove_image"]:
            model_data.pop(field, None)

        is_enabled = temp_data.get("is_enabled")
        if is_enabled is None:
            is_enabled = data.pop("is_enabled", None)
        if is_enabled is not None:
            model_data["is_enabled"] = is_enabled

        try:

            return await super().update_model(request, pk, model_data)
        except Exception as e:
            rollback = getattr(request.state, "product_image_to_rollback", None)
            if rollback:
                import os
                if os.path.isfile(rollback):
                    os.remove(rollback)
            raise e
    async def on_model_change(self, data, model, is_created, request):
        from starlette.datastructures import UploadFile
        import os
        import uuid
        import logging
        from bot.misc.env import EnvKeys
        from starlette.exceptions import HTTPException
        temp_data = getattr(request.state, "temp_form_data", {})
        image_upload = temp_data.get("image_upload")
        if image_upload is None:
            image_upload = data.pop("image_upload", None)
        remove_image = temp_data.get("remove_image", False)
        if not remove_image:
            remove_image = data.pop("remove_image", False)
        old_image_path = getattr(model, "image_path", None)
        request.state.product_image_to_delete = None
        request.state.product_image_to_rollback = None
        base_dir = os.path.abspath(EnvKeys.PRODUCT_IMAGES_ROOT)

        relative_path, absolute_path = await handle_managed_image_upload(
            image_upload, base_dir, "product_images"
        )

        if relative_path:
            model.image_path = relative_path
            if old_image_path:
                request.state.product_image_to_delete = old_image_path
            request.state.product_image_to_rollback = absolute_path
        elif remove_image:
            model.image_path = None
            if old_image_path:
                request.state.product_image_to_delete = old_image_path
        existing_manual = dict(getattr(model, "manual_instructions_i18n", {}) or {})
        for lang in ["en", "ar", "ru", "zh", "vi", "tr", "es"]:
            val = temp_data.get(f"manual_instr_{lang}")
            if val is None and f"manual_instr_{lang}" in data:
                val = data.pop(f"manual_instr_{lang}")
            if val is not None:
                existing_manual[lang] = val.strip()
        model.manual_instructions_i18n = existing_manual if existing_manual else None

        existing_intro = dict(getattr(model, "customer_input_intro_i18n", {}) or {})
        for lang in ["en", "ar", "ru", "zh", "vi", "tr", "es"]:
            val = temp_data.get(f"input_intro_{lang}")
            if val is None and f"input_intro_{lang}" in data:
                val = data.pop(f"input_intro_{lang}")
            if val is not None:
                existing_intro[lang] = val.strip()
        model.customer_input_intro_i18n = existing_intro if existing_intro else None

        preset = temp_data.get("eta_preset")
        if preset is None:
            preset = data.pop("eta_preset", None)
        if preset and preset != "custom":
            model.fulfillment_eta_minutes = int(preset)
        elif not preset:
            model.fulfillment_eta_minutes = None

        popular_order = getattr(model, "popular_deal_order", None)
        if popular_order is not None and popular_order < 0:
            from starlette.exceptions import HTTPException
            raise HTTPException(status_code=400, detail="Popular Deal Order cannot be negative.")

        if getattr(super(), "on_model_change", None):
            await super().on_model_change(data, model, is_created, request)

    async def after_model_change(self, data, model, is_created, request):
        old_image = getattr(request.state, "product_image_to_delete", None)
        if old_image:
            from bot.misc.utils import resolve_product_image_path
            cleanup_orphaned_image(old_image, resolve_product_image_path)
        if getattr(super(), "after_model_change", None):
            await super().after_model_change(data, model, is_created, request)
    async def on_model_delete(self, model, request):
        from bot.database import Database
        from bot.database.models import OrderItem, BoughtGoods
        from bot.database.models.main import CartItems, CheckoutIntakeDraft
        from sqlalchemy import select, delete
        from starlette.exceptions import HTTPException

        async with Database().session() as session:
            # 1. Check commercial blockers
            has_order_item = (await session.execute(
                select(OrderItem).where(OrderItem.item_id == model.id).limit(1)
            )).scalar_one_or_none()

            has_bought_goods = (await session.execute(
                select(BoughtGoods).where(BoughtGoods.item_name == model.name).limit(1)
            )).scalar_one_or_none()

            has_consumed_draft = (await session.execute(
                select(CheckoutIntakeDraft).where(
                    CheckoutIntakeDraft.goods_id == model.id,
                    CheckoutIntakeDraft.status == 'consumed'
                ).limit(1)
            )).scalar_one_or_none()

            # 2. Block if referenced by commercial history
            if has_order_item or has_bought_goods or has_consumed_draft:
                raise HTTPException(status_code=400, detail="Cannot delete this product because it is referenced by historical orders. Disable the product instead to hide it from the shop.")

            # 3. Clean up temporary records that don't cascade natively
            await session.execute(
                delete(CartItems).where(CartItems.item_name == model.name)
            )
            await session.commit()

        if getattr(super(), "on_model_delete", None):
            await super().on_model_delete(model, request)


    async def after_model_delete(self, model, request):
        # Clean up product image from disk only after successful DB deletion
        image_path = getattr(model, 'image_path', None)
        if image_path:
            from bot.misc.utils import resolve_product_image_path
            cleanup_orphaned_image(image_path, resolve_product_image_path)
        if getattr(super(), "after_model_delete", None):
            await super().after_model_delete(model, request)
class CustomerFieldBaseForm(Form):
    preset = SelectField("Preset", choices=[
        ("", "Custom Field"),
        ("email", "Email Activation"),
        ("username", "Username Activation"),
        ("url", "Account URL"),
        ("phone", "Phone Number"),
        ("secret", "Secret / Password")
    ], validate_choice=False, render_kw={"class": "form-select", "id": "preset"})

    label_en = StringField("Label - English", render_kw={"class": "form-control"})
    label_ar = StringField("Label - Arabic", render_kw={"class": "form-control"})
    label_ru = StringField("Label - Russian", render_kw={"class": "form-control"})
    label_zh = StringField("Label - Chinese", render_kw={"class": "form-control"})
    label_vi = StringField("Label - Vietnamese", render_kw={"class": "form-control"})
    label_tr = StringField("Label - Turkish", render_kw={"class": "form-control"})
    label_es = StringField("Label - Spanish", render_kw={"class": "form-control"})
    placeholder_en = StringField("Placeholder - English", render_kw={"class": "form-control"})
    placeholder_ar = StringField("Placeholder - Arabic", render_kw={"class": "form-control"})
    placeholder_ru = StringField("Placeholder - Russian", render_kw={"class": "form-control"})
    placeholder_zh = StringField("Placeholder - Chinese", render_kw={"class": "form-control"})
    placeholder_vi = StringField("Placeholder - Vietnamese", render_kw={"class": "form-control"})
    placeholder_tr = StringField("Placeholder - Turkish", render_kw={"class": "form-control"})
    placeholder_es = StringField("Placeholder - Spanish", render_kw={"class": "form-control"})
    help_text_en = StringField("Help Text - English", render_kw={"class": "form-control"})
    help_text_ar = StringField("Help Text - Arabic", render_kw={"class": "form-control"})
    help_text_ru = StringField("Help Text - Russian", render_kw={"class": "form-control"})
    help_text_zh = StringField("Help Text - Chinese", render_kw={"class": "form-control"})
    help_text_vi = StringField("Help Text - Vietnamese", render_kw={"class": "form-control"})
    help_text_tr = StringField("Help Text - Turkish", render_kw={"class": "form-control"})
    help_text_es = StringField("Help Text - Spanish", render_kw={"class": "form-control"})
    select_options_raw = HiddenField("Select Options JSON", default="[]", render_kw={"id": "select_options_raw"})

    def process(self, formdata=None, obj=None, data=None, **kwargs):
        if obj and not formdata:
            if getattr(obj, "label_i18n", None):
                for lang in ["en", "ar", "ru", "zh", "vi", "tr", "es"]:
                    kwargs[f'label_{lang}'] = obj.label_i18n.get(lang, '')
            if getattr(obj, "placeholder_i18n", None):
                for lang in ["en", "ar", "ru", "zh", "vi", "tr", "es"]:
                    kwargs[f'placeholder_{lang}'] = obj.placeholder_i18n.get(lang, '')
            if getattr(obj, "help_text_i18n", None):
                for lang in ["en", "ar", "ru", "zh", "vi", "tr", "es"]:
                    kwargs[f'help_text_{lang}'] = obj.help_text_i18n.get(lang, '')
            if getattr(obj, "select_options_i18n", None):
                arr = [{"key": k, "en": v.get("en", ""), "ar": v.get("ar", ""), "ru": v.get("ru", ""), "zh": v.get("zh", ""), "vi": v.get("vi", ""), "tr": v.get("tr", ""), "es": v.get("es", "")} for k, v in obj.select_options_i18n.items()]
                kwargs['select_options_raw'] = json.dumps(arr)

        if not obj and not formdata:
            if 'required' not in kwargs:
                kwargs['required'] = True
            if 'is_active' not in kwargs:
                kwargs['is_active'] = True

        super().process(formdata, obj, data, **kwargs)

        if formdata:
            preset = self.preset.data if hasattr(self, 'preset') else None
            fk = self.field_key.data if hasattr(self, 'field_key') else None
            if preset and not fk:
                presets = {
                    "email": "email",
                    "username": "username",
                    "url": "account_url",
                    "phone": "phone",
                    "secret": "password"
                }
                if preset in presets:
                    self.field_key.data = presets[preset]
                    if hasattr(self.field_key, 'raw_data'):
                        self.field_key.raw_data = [presets[preset]]

    def validate_select_options_raw(form, field):
        if form.field_type.data != 'select':
            return

        try:
            options = json.loads(field.data)
        except json.JSONDecodeError:
            raise ValidationError("Invalid JSON format for select options.")

        if not isinstance(options, list):
            raise ValidationError("Select options must be a list of objects.")

        if not options:
            raise ValidationError("Select fields require at least one valid option.")

        if len(options) > 100:
            raise ValidationError("Too many options.")

        seen_keys = set()
        for opt in options:
            if not isinstance(opt, dict):
                raise ValidationError("Each option must be an object.")
            key = opt.get('key')
            en = opt.get('en')
            if not key or not isinstance(key, str) or len(key) > 64:
                raise ValidationError("Invalid or missing option key.")
            if not key.isalnum() and not all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in key):
                raise ValidationError("Option key contains invalid characters.")
            if key in seen_keys:
                raise ValidationError(f"Duplicate option key: {key}")
            seen_keys.add(key)
            if not en or not isinstance(en, str) or len(en) > 128:
                raise ValidationError("English label is required and must be under 128 characters.")
            if 'ar' in opt and opt['ar'] and (not isinstance(opt['ar'], str) or len(opt['ar']) > 128):
                raise ValidationError("Arabic label must be a string under 128 characters.")


class ProductCustomerFieldAdmin(AuditModelView, model=ProductCustomerField):
    column_list = [
        ProductCustomerField.id,
        ProductCustomerField.goods,
        ProductCustomerField.field_key,
        ProductCustomerField.field_type,
        ProductCustomerField.required,
        ProductCustomerField.is_sensitive,
        ProductCustomerField.scope,
        ProductCustomerField.sort_order,
        ProductCustomerField.is_active
    ]
    column_searchable_list = [ProductCustomerField.field_key]
    column_sortable_list = [ProductCustomerField.id, ProductCustomerField.sort_order, ProductCustomerField.is_active]
    form_columns = [
        ProductCustomerField.goods,
        ProductCustomerField.field_key,
        ProductCustomerField.field_type,
        ProductCustomerField.scope,
        ProductCustomerField.required,
        ProductCustomerField.is_sensitive,
        ProductCustomerField.is_active,
        ProductCustomerField.sort_order,
        ProductCustomerField.min_length,
        ProductCustomerField.max_length
    ]
    form_base_class = CustomerFieldBaseForm
    create_template = "admin/customer_field_create.html"
    edit_template = "admin/customer_field_edit.html"

    name = "Customer Field"
    name_plural = "Customer Fields"
    icon = "fa-solid fa-keyboard"
    can_export = False

    form_overrides = {
        "field_type": SelectField,
        "scope": SelectField
    }
    form_args = {
        "field_type": {
            "choices": [
                ("text", "Text (Single line)"),
                ("textarea", "Text Area (Multi line)"),
                ("email", "Email Address"),
                ("phone", "Phone Number"),
                ("username", "Username"),
                ("url", "URL/Link"),
                ("select", "Choice List"),
                ("secret", "Secret/Password")
            ]
        },
        "scope": {
            "choices": [
                ("per_order", "Per Order (Once)"),
                ("per_unit", "Per Unit (Multiplier)")
            ]
        }
    }

    async def on_model_change(self, data, model, is_created, request):
        data.pop("preset", None)
        field_type = data.get("field_type")

        if field_type == "secret":
            model.is_sensitive = True

        if field_type == "select":
            raw_options = data.pop("select_options_raw", None)
            if not raw_options or raw_options == "[]":
                raise ValidationError("Choice List fields require at least one option.")

            options_list = json.loads(raw_options)
            if not options_list:
                raise ValidationError("Choice List fields require at least one option.")

            final_options = {}
            for opt in options_list:
                key = opt.get("key", "").strip()
                en_label = opt.get("en", "").strip()
                if not key or not en_label:
                    raise ValidationError("Each option must have a stable option key and an English label.")
                translations = {"en": en_label}
                for lang in ["ar", "ru", "zh", "vi", "tr", "es"]:
                    if lang in opt and opt.get(lang) is not None:
                        translations[lang] = str(opt.get(lang)).strip()
                final_options[key] = translations

            model.select_options_i18n = final_options
        else:
            model.select_options_i18n = None
            data.pop("select_options_raw", None)

        try:
            if is_created and model.sort_order is None and model.goods_id:
                from sqlalchemy import select, func
                async with Database().session() as session:
                    max_val = await session.scalar(
                        select(func.max(ProductCustomerField.sort_order)).where(ProductCustomerField.goods_id == model.goods_id)
                    )
                model.sort_order = (max_val or 0) + 1
        except Exception as e:
            import traceback
            open('traceback.txt', 'w').write(traceback.format_exc())
            raise e

        def _update_i18n(attr_name, vals):
            existing = dict(getattr(model, attr_name, {}) or {})
            for lang, val in vals.items():
                if val is not None:
                    existing[lang] = val.strip()
            setattr(model, attr_name, existing if existing else None)

        label_vals = {l: data.pop(f"label_{l}") for l in ["en", "ar", "ru", "zh", "vi", "tr", "es"] if f"label_{l}" in data}
        placeholder_vals = {l: data.pop(f"placeholder_{l}") for l in ["en", "ar", "ru", "zh", "vi", "tr", "es"] if f"placeholder_{l}" in data}
        help_text_vals = {l: data.pop(f"help_text_{l}") for l in ["en", "ar", "ru", "zh", "vi", "tr", "es"] if f"help_text_{l}" in data}

        _update_i18n("label_i18n", label_vals)
        _update_i18n("placeholder_i18n", placeholder_vals)
        _update_i18n("help_text_i18n", help_text_vals)

        if getattr(super(), "on_model_change", None):
            await super().on_model_change(data, model, is_created, request)



class ItemValuesAdmin(AuditModelView, model=ItemValues):
    column_list = [ItemValues.id, ItemValues.item_id, ItemValues.value, ItemValues.is_infinity]
    column_searchable_list = [ItemValues.value]
    column_sortable_list = [ItemValues.id, ItemValues.item_id]
    name = "Stock Item"
    name_plural = "Stock Items"
    icon = "fa-solid fa-warehouse"


class BoughtGoodsAdmin(ModelView, model=BoughtGoods):
    column_list = [BoughtGoods.id, BoughtGoods.item_name, BoughtGoods.value,
                   BoughtGoods.price, BoughtGoods.buyer_id, BoughtGoods.bought_datetime,
                   BoughtGoods.unique_id]
    column_searchable_list = [BoughtGoods.item_name, BoughtGoods.buyer_id, BoughtGoods.unique_id]
    column_sortable_list = [BoughtGoods.id, BoughtGoods.bought_datetime, BoughtGoods.price]
    column_default_sort = (BoughtGoods.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Delivered Item"
    name_plural = "Delivered Items"
    icon = "fa-solid fa-cart-shopping"


class OperationsAdmin(ModelView, model=Operations):
    column_list = [Operations.id, Operations.user_id, Operations.operation_value,
                   Operations.operation_time]
    column_searchable_list = [Operations.user_id]
    column_sortable_list = [Operations.id, Operations.operation_time, Operations.operation_value]
    column_default_sort = (Operations.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Operation"
    name_plural = "Operations"
    icon = "fa-solid fa-money-bill-transfer"


class PaymentsAdmin(ModelView, model=Payments):
    column_list = [Payments.id, Payments.provider, Payments.external_id, Payments.user_id,
                   Payments.amount, Payments.currency, Payments.status, Payments.created_at]
    column_searchable_list = [Payments.user_id, Payments.external_id, Payments.provider]
    column_sortable_list = [Payments.id, Payments.created_at, Payments.amount, Payments.status]
    column_default_sort = (Payments.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Payment"
    name_plural = "Payments"
    icon = "fa-solid fa-credit-card"


class ReferralEarningsAdmin(ModelView, model=ReferralEarnings):
    column_list = [ReferralEarnings.id, ReferralEarnings.referrer_id,
                   ReferralEarnings.referral_id, ReferralEarnings.amount,
                   ReferralEarnings.original_amount, ReferralEarnings.created_at]
    column_searchable_list = [ReferralEarnings.referrer_id, ReferralEarnings.referral_id]
    column_sortable_list = [ReferralEarnings.id, ReferralEarnings.created_at, ReferralEarnings.amount]
    column_default_sort = (ReferralEarnings.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Referral Earning"
    name_plural = "Referral Earnings"
    icon = "fa-solid fa-handshake"


class AuditLogAdmin(ModelView, model=AuditLog):
    column_list = [AuditLog.id, AuditLog.timestamp, AuditLog.level, AuditLog.user_id,
                   AuditLog.action, AuditLog.resource_type, AuditLog.resource_id,
                   AuditLog.details, AuditLog.ip_address]
    column_searchable_list = [AuditLog.action, AuditLog.resource_type, AuditLog.details]
    column_sortable_list = [AuditLog.id, AuditLog.timestamp, AuditLog.level, AuditLog.action]
    column_default_sort = (AuditLog.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Audit Log"
    name_plural = "Audit Logs"
    icon = "fa-solid fa-clipboard-list"


class PromoCodeAdmin(AuditModelView, model=PromoCodes):
    column_list = [PromoCodes.id, PromoCodes.code, PromoCodes.discount_type,
                   PromoCodes.discount_value, PromoCodes.max_uses, PromoCodes.current_uses,
                   PromoCodes.is_active, PromoCodes.expires_at, PromoCodes.created_at]
    column_searchable_list = [PromoCodes.code]
    column_sortable_list = [PromoCodes.id, PromoCodes.code, PromoCodes.created_at]
    column_default_sort = (PromoCodes.id, True)
    name = "Promo Code"
    name_plural = "Promo Codes"
    icon = "fa-solid fa-tag"


class CartItemsAdmin(ModelView, model=CartItems):
    column_list = [CartItems.id, CartItems.user_id, CartItems.item_name, CartItems.added_at]
    column_searchable_list = [CartItems.user_id, CartItems.item_name]
    column_sortable_list = [CartItems.id, CartItems.added_at]
    column_default_sort = (CartItems.id, True)
    can_create = False
    can_edit = False
    can_delete = False
    name = "Cart Item"
    name_plural = "Cart Items"
    icon = "fa-solid fa-cart-plus"



class ReviewsAdmin(AuditModelView, model=Reviews):
    column_list = [
        Reviews.id, Reviews.user_id, Reviews.product_id, Reviews.order_id,
        Reviews.rating, Reviews.status, Reviews.is_featured, Reviews.created_at
    ]
    column_searchable_list = [Reviews.user_id, Reviews.item_name, Reviews.status]
    column_sortable_list = [Reviews.id, Reviews.rating, Reviews.created_at, Reviews.status, Reviews.is_featured]
    column_default_sort = (Reviews.id, True)
    name = "Review"
    name_plural = "Reviews"
    icon = "fa-solid fa-star"

    form_columns = [
        Reviews.status,
        Reviews.is_featured,
        Reviews.admin_reply
    ]

    form_overrides = {
        "status": SelectField
    }

    form_args = dict(
        status=dict(choices=[
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('hidden', 'Hidden')
        ])
    )

    async def after_model_change(self, data: dict, model: Any, is_created: bool, request: Request) -> None:
        if not is_created:
            # Check if status/featured changed
            pass # Simplified auditing for now
        await super().after_model_change(data, model, is_created, request)


class ProductRestockSubscriptionAdmin(ModelView, model=ProductRestockSubscription):
    column_list = [
        ProductRestockSubscription.id, ProductRestockSubscription.item_id,
        ProductRestockSubscription.user_id, ProductRestockSubscription.status,
        ProductRestockSubscription.attempts, ProductRestockSubscription.created_at,
        ProductRestockSubscription.updated_at, ProductRestockSubscription.notified_at,
        ProductRestockSubscription.cancelled_at
    ]
    column_searchable_list = [ProductRestockSubscription.item_id]
    column_sortable_list = [ProductRestockSubscription.id, ProductRestockSubscription.created_at]

    # Filterable list
    column_details_list = column_list
    can_create = False
    can_edit = False
    can_delete = False
    name = "Restock Alert"
    name_plural = "Restock Alerts"
    icon = "fa-solid fa-bell"

class OrdersAdmin(ModelView, model=Order):
    column_list = [Order.id, Order.public_id, Order.user_id, Order.status, Order.currency, Order.total, Order.created_at]
    column_searchable_list = [Order.public_id, Order.user_id]
    column_sortable_list = [Order.id, Order.created_at]
    can_create = False
    can_edit = False
    can_delete = False
    name = "Order"
    name_plural = "Orders"
    icon = "fa-solid fa-box"

class OrderItemsAdmin(ModelView, model=OrderItem):
    column_list = [OrderItem.id, OrderItem.order_id, OrderItem.item_id, OrderItem.product_name_snapshot, OrderItem.quantity, OrderItem.total, OrderItem.fulfillment_status]
    column_searchable_list = [OrderItem.order_id, OrderItem.item_id, OrderItem.product_name_snapshot]
    column_sortable_list = [OrderItem.id, OrderItem.created_at]
    can_create = False
    can_edit = False
    can_delete = False
    name = "Order Item"
    name_plural = "Order Items"
    icon = "fa-solid fa-boxes-stacked"

class CheckoutIntakeDraftAdmin(ModelView, model=CheckoutIntakeDraft):
    column_list = [
        CheckoutIntakeDraft.id, CheckoutIntakeDraft.order_id, CheckoutIntakeDraft.user_id, CheckoutIntakeDraft.goods_id,
        CheckoutIntakeDraft.quantity, CheckoutIntakeDraft.status,
        CheckoutIntakeDraft.current_step, CheckoutIntakeDraft.created_at, CheckoutIntakeDraft.updated_at, CheckoutIntakeDraft.expires_at
    ]
    column_searchable_list = [CheckoutIntakeDraft.user_id, CheckoutIntakeDraft.goods_id]
    column_sortable_list = [CheckoutIntakeDraft.id, CheckoutIntakeDraft.created_at]
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    column_details_exclude_list = ["encrypted_payload", "public_token", "schema_fingerprint", "encryption_version"]
    name = "Checkout Draft"

    def _format_status(model, name):
        val = getattr(model, name)
        if val == "pending":
            return Markup('<span style="color:#eab308;font-weight:bold">Pending</span>')
        elif val == "completed":
            return Markup('<span style="color:#22c55e;font-weight:bold">Completed</span>')
        elif val == "expired":
            return Markup('<span style="color:#ef4444">Expired</span>')
        elif val == "invalidated":
            return Markup('<span style="color:#64748b">Invalidated</span>')
        elif val == "cancelled":
            return Markup('<span style="color:#94a3b8">Cancelled</span>')
        return val

    column_formatters = {
        "status": _format_status,
    }

    name_plural = "Checkout Drafts"
    icon = "fa-solid fa-file-pen"
    category = "System Diagnostic"

def _get_input(model: ManualFulfillmentJob, key: str):
    if not model.order_item or not model.order_item.customer_inputs:
        return None
    for inp in model.order_item.customer_inputs:
        if inp.field_key_snapshot == key:
            return inp
    return None

class ManualFulfillmentJobAdmin(ModelView, model=ManualFulfillmentJob):
    column_list = [
        ManualFulfillmentJob.id,
        "public_order_id",
        "product_name",
        "customer",
        "quantity",
        ManualFulfillmentJob.status,
        "submitted_email",
        "password_status",
        "paid_at",
        "estimated_delivery"
    ]
    column_labels = {
        "public_order_id": "Public Order ID",
        "product_name": "Product Name",
        "customer": "Customer",
        "quantity": "Quantity",
        "submitted_email": "Email",
        "password_status": "Password Status",
        "paid_at": "Paid At",
        "estimated_delivery": "Est. Delivery"
    }
    column_formatters = {
        "public_order_id": lambda m, a: m.order_item.order.public_id if m.order_item and m.order_item.order else "—",
        "product_name": lambda m, a: m.order_item.product_name_snapshot if m.order_item else "—",
        "customer": lambda m, a: f"User {m.order_item.order.user.telegram_id}" if m.order_item and m.order_item.order and m.order_item.order.user else "—",
        "quantity": lambda m, a: str(m.order_item.quantity) if m.order_item else "—",
        "submitted_email": lambda m, a: (_get_input(m, 'email').masked_preview if _get_input(m, 'email') else "—"),
        "password_status": lambda m, a: ("Submitted ✅" if _get_input(m, 'password') else "Not Submitted"),
        "paid_at": lambda m, a: m.order_item.order.created_at.strftime("%Y-%m-%d %H:%M") if m.order_item and m.order_item.order and m.order_item.order.created_at else "—",
        "estimated_delivery": lambda m, a: "—"  # Not currently stored in DB explicitly
    }
    column_formatters_detail = column_formatters

    def list_query(self, request: Request):
        from sqlalchemy.orm import selectinload
        return super().list_query(request).options(
            selectinload(ManualFulfillmentJob.order_item).selectinload(OrderItem.order).selectinload(Order.user),
            selectinload(ManualFulfillmentJob.order_item).selectinload(OrderItem.customer_inputs)
        )

    def details_query(self, request: Request):
        from sqlalchemy.orm import selectinload
        return super().details_query(request).options(
            selectinload(ManualFulfillmentJob.order_item).selectinload(OrderItem.order).selectinload(Order.user),
            selectinload(ManualFulfillmentJob.order_item).selectinload(OrderItem.customer_inputs)
        )

    async def on_model_change(self, data, model, is_created, request):
        from sqlalchemy import select, func
        from bot.database.main import Database
        from bot.database.models.main import ManualOrderConversationSession

        # If status changed to cancelled, close any active conversation session
        if not is_created and getattr(model, 'status', None) == 'cancelled':
            async with Database().session() as session:
                active_sessions = await session.execute(
                    select(ManualOrderConversationSession).filter(
                        ManualOrderConversationSession.fulfillment_job_id == model.id,
                        ManualOrderConversationSession.status == 'active'
                    )
                )
                for s in active_sessions.scalars():
                    s.status = 'closed'
                    s.closed_at = func.now()
                    session.add(s)
                await session.commit()

        if getattr(super(), "on_model_change", None):
            await super().on_model_change(data, model, is_created, request)

    column_searchable_list = [ManualFulfillmentJob.order_item_id, ManualFulfillmentJob.status]
    column_sortable_list = [ManualFulfillmentJob.id, ManualFulfillmentJob.created_at, ManualFulfillmentJob.updated_at]
    can_create = False
    can_edit = False
    can_delete = False
    name = "Manual Order"
    name_plural = "Manual Orders"
    icon = "fa-solid fa-clipboard-list"


# Health & Metrics Endpoints
async def health_check(request: Request) -> JSONResponse:
    health_status = {
        "status": "healthy",
        "checks": {},
    }

    try:
        async with Database().session() as s:
            await s.execute(text("SELECT 1"))
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        logger.error(f"Health check database error: {e}")
        health_status["checks"]["database"] = "error"
        health_status["status"] = "unhealthy"

    cache = get_cache_manager()
    if cache:
        health_status["checks"]["redis"] = "ok" if cache._healthy else "degraded"
    else:
        health_status["checks"]["redis"] = "not configured"

    metrics = get_metrics()
    if metrics:
        health_status["checks"]["metrics"] = "ok"
        health_status["uptime"] = metrics.get_metrics_summary()["uptime_seconds"]

    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(health_status, status_code=status_code)


async def prometheus_metrics(request: Request) -> PlainTextResponse:
    if not request.session.get("authenticated"):
        return PlainTextResponse("Unauthorized", status_code=401)
    metrics = get_metrics()
    if not metrics:
        return PlainTextResponse("# Metrics not initialized\n", status_code=503)
    return PlainTextResponse(metrics.export_to_prometheus(), media_type="text/plain")


async def metrics_json(request: Request) -> JSONResponse:
    if not request.session.get("authenticated"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    metrics = get_metrics()
    if not metrics:
        return JSONResponse({"error": "Metrics not initialized"}, status_code=503)
    return JSONResponse(metrics.get_metrics_summary(), status_code=200)


from starlette.datastructures import FormData
class ShopAdmin(Admin):
    async def _handle_form_data(self, request: Request, obj: Any = None) -> FormData:
        is_goods = isinstance(obj, Goods) or (obj is None and "/goods/create" in request.url.path)
        is_category = isinstance(obj, Categories) or (obj is None and "/category/create" in request.url.path)
        if is_goods or is_category:
            form = await request.form()
            temp_data = getattr(request.state, "temp_form_data", {})
            temp_data["image_upload"] = form.get("image_upload")
            if "remove_image" in form:
                temp_data["remove_image"] = form.get("remove_image")
            request.state.temp_form_data = temp_data
            new_items = []
            for k, v in form.multi_items():
                if k not in ("image_upload", "remove_image"):
                    new_items.append((k, v))
            request._form = FormData(new_items)
        return await super()._handle_form_data(request, obj)

    async def delete(self, request: Request) -> Response:
        identity = request.path_params.get("identity")
        if identity == "goods":
            params = request.query_params.get("pks", "")
            pks = params.split(",") if params else []
            if len(pks) > 1:
                from bot.database import Database
                from bot.database.models import OrderItem, BoughtGoods
                from bot.database.models.main import CheckoutIntakeDraft
                from sqlalchemy import select
                from starlette.exceptions import HTTPException
                model_view = self._find_model_view(identity)
                async with Database().session() as session:
                    for pk in pks:
                        model = await model_view.get_object_for_delete(pk)
                        if not model:
                            continue
                        has_order_item = (await session.execute(
                            select(OrderItem).where(OrderItem.item_id == model.id).limit(1)
                        )).scalar_one_or_none()
                        has_bought_goods = (await session.execute(
                            select(BoughtGoods).where(BoughtGoods.item_name == model.name).limit(1)
                        )).scalar_one_or_none()
                        has_consumed_draft = (await session.execute(
                            select(CheckoutIntakeDraft).where(
                                CheckoutIntakeDraft.goods_id == model.id,
                                CheckoutIntakeDraft.status == 'consumed'
                            ).limit(1)
                        )).scalar_one_or_none()

                        if has_order_item or has_bought_goods or has_consumed_draft:
                            raise HTTPException(status_code=400, detail="Cannot delete products: at least one selected product is referenced by existing commercial history (orders or purchases). Disable the product instead to hide it from the shop.")
        return await super().delete(request)

# App Factory
def create_admin_app() -> Starlette:

    from bot.web.export import export_routes
    from bot.web.fulfillment import fulfillment_routes

    routes = [
        Route("/health", health_check),
        Route("/metrics", metrics_json),
        Route("/metrics/prometheus", prometheus_metrics),
    ] + fulfillment_routes + export_routes

    app = Starlette(routes=routes)

    from starlette.exceptions import HTTPException
    from starlette.responses import PlainTextResponse

    async def http_exception_handler(request: Request, exc: HTTPException):
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    app.add_exception_handler(HTTPException, http_exception_handler)


    app.add_middleware(SessionMiddleware, secret_key=EnvKeys.SECRET_KEY, max_age=1800)

    auth_backend = AdminAuth(secret_key=EnvKeys.SECRET_KEY)
    admin = ShopAdmin(
        app,
        engine=Database().engine,
        authentication_backend=auth_backend,
        title="Telegram Shop Admin",
        templates_dir="bot/web/templates",
    )
    admin.admin.add_exception_handler(HTTPException, http_exception_handler)
    app.state.admin = admin

    admin.add_view(UserAdmin)
    admin.add_view(RoleAdmin)
    admin.add_view(CategoryAdmin)
    admin.add_view(GoodsAdmin)
    admin.add_view(ProductCustomerFieldAdmin)
    from bot.web.quick_field_set import QuickFieldSetView
    admin.add_view(QuickFieldSetView)
    admin.add_view(ItemValuesAdmin)
    admin.add_view(BoughtGoodsAdmin)
    admin.add_view(OperationsAdmin)
    admin.add_view(PaymentsAdmin)
    admin.add_view(ReferralEarningsAdmin)
    admin.add_view(AuditLogAdmin)
    admin.add_view(PromoCodeAdmin)
    admin.add_view(CartItemsAdmin)
    admin.add_view(StoreSettingsAdmin)
    admin.add_view(MainMenuButtonSettingsAdmin)
    admin.add_view(ProductRestockSubscriptionAdmin)
    admin.add_view(OrdersAdmin)
    admin.add_view(OrderItemsAdmin)
    admin.add_view(CheckoutIntakeDraftAdmin)
    admin.add_view(ManualFulfillmentJobAdmin)

    if EnvKeys.REVIEWS_ENABLED == "1":
        admin.add_view(ReviewsAdmin)

    return app
