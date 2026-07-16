from wtforms import ValidationError
import logging
import time
from typing import Any

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route
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
    column_list = [User.telegram_id, User.balance, User.role_id, User.referral_id,
                   User.registration_date, User.is_blocked]
    column_searchable_list = [User.telegram_id]
    column_sortable_list = [User.telegram_id, User.balance, User.registration_date]
    column_default_sort = (User.registration_date, True)
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"


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


class CategoryAdmin(AuditModelView, model=Categories):
    column_list = [Categories.id, Categories.name, Categories.description, Categories.parent_id]
    column_searchable_list = [Categories.name]
    column_sortable_list = [Categories.id, Categories.name]
    name = "Category"
    name_plural = "Categories"
    icon = "fa-solid fa-folder"
    form_columns = [Categories.name, Categories.description, Categories.parent]


class StoreSettingsAdmin(AuditModelView, model=StoreSettings):
    column_list = [StoreSettings.id, StoreSettings.shop_root_title, StoreSettings.main_menu_title]
    name = "Store Setting"
    name_plural = "Store Settings"
    icon = "fa-solid fa-gear"
    can_create = False
    can_delete = False

    from sqladmin.fields import FileField
    from starlette.datastructures import UploadFile
    import os
    import shutil
    import uuid

    form_columns = [
        StoreSettings.shop_root_title,
        StoreSettings.shop_root_description,
        StoreSettings.main_menu_title,
        StoreSettings.main_menu_description,
        StoreSettings.main_menu_footer,
        StoreSettings.main_menu_image_path,
        StoreSettings.main_menu_image_url,
        StoreSettings.root_category_columns,
        StoreSettings.subcategory_columns,
        StoreSettings.product_columns
    ]

    form_overrides = {
        "root_category_columns": SelectField,
        "subcategory_columns": SelectField,
        "product_columns": SelectField,
    }

    form_args = {
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
    form_columns = [MainMenuButtonSettings.label_en, MainMenuButtonSettings.label_ar,
                    MainMenuButtonSettings.row_order, MainMenuButtonSettings.column_order,
                    MainMenuButtonSettings.is_enabled]
    can_create = False
    can_delete = False
    name = "Menu Button"
    name_plural = "Menu Buttons"
    icon = "fa-solid fa-bars"


from wtforms import Form, StringField, TextAreaField, SelectField, HiddenField
import json

class GoodsBaseForm(Form):
    manual_instr_en = TextAreaField("Manual Instructions - English", render_kw={"class": "form-control"})
    manual_instr_ar = TextAreaField("Manual Instructions - Arabic", render_kw={"class": "form-control"})
    input_intro_en = TextAreaField("Customer Input Intro - English", render_kw={"class": "form-control"})
    input_intro_ar = TextAreaField("Customer Input Intro - Arabic", render_kw={"class": "form-control"})
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

    def process(self, formdata=None, obj=None, data=None, **kwargs):
        if obj and not formdata:
            if obj.manual_instructions_i18n:
                kwargs['manual_instr_en'] = obj.manual_instructions_i18n.get('en', '')
                kwargs['manual_instr_ar'] = obj.manual_instructions_i18n.get('ar', '')
            if obj.customer_input_intro_i18n:
                kwargs['input_intro_en'] = obj.customer_input_intro_i18n.get('en', '')
                kwargs['input_intro_ar'] = obj.customer_input_intro_i18n.get('ar', '')
            if obj.fulfillment_eta_minutes is not None:
                preset = str(obj.fulfillment_eta_minutes)
                if preset in ["60", "180", "360", "720", "1440", "2880"]:
                    kwargs['eta_preset'] = preset
                else:
                    kwargs['eta_preset'] = "custom"
        super().process(formdata, obj, data, **kwargs)

class GoodsAdmin(AuditModelView, model=Goods):
    column_list = [Goods.id, Goods.name, Goods.price, Goods.category_id, Goods.fulfillment_mode]
    column_searchable_list = [Goods.name]
    column_sortable_list = [Goods.id, Goods.name, Goods.price, Goods.fulfillment_mode]
    form_columns = [
        Goods.name, Goods.price, Goods.description, Goods.category,
        Goods.fulfillment_mode, Goods.fulfillment_eta_minutes
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

    async def on_model_change(self, data, model, is_created, request):
        existing_manual = dict(getattr(model, "manual_instructions_i18n", {}) or {})
        en_instr = data.pop("manual_instr_en", None)
        ar_instr = data.pop("manual_instr_ar", None)

        if en_instr:
            existing_manual["en"] = en_instr
        elif "en" in existing_manual and not en_instr:
            del existing_manual["en"]

        if ar_instr:
            existing_manual["ar"] = ar_instr
        elif "ar" in existing_manual and not ar_instr:
            del existing_manual["ar"]

        model.manual_instructions_i18n = existing_manual if existing_manual else None

        existing_intro = dict(getattr(model, "customer_input_intro_i18n", {}) or {})
        en_intro = data.pop("input_intro_en", None)
        ar_intro = data.pop("input_intro_ar", None)

        if en_intro:
            existing_intro["en"] = en_intro
        elif "en" in existing_intro and not en_intro:
            del existing_intro["en"]

        if ar_intro:
            existing_intro["ar"] = ar_intro
        elif "ar" in existing_intro and not ar_intro:
            del existing_intro["ar"]

        model.customer_input_intro_i18n = existing_intro if existing_intro else None

        preset = data.pop("eta_preset", None)
        if preset and preset != "custom":
            model.fulfillment_eta_minutes = int(preset)
        elif not preset:
            model.fulfillment_eta_minutes = None

        if getattr(super(), "on_model_change", None):
            await super().on_model_change(data, model, is_created, request)

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
                raise HTTPException(status_code=400, detail="Cannot delete product: it is referenced by existing commercial history (orders or purchases).")

            # 3. Clean up temporary records that don't cascade natively
            await session.execute(
                delete(CartItems).where(CartItems.item_name == model.name)
            )
            await session.commit()

        if getattr(super(), "on_model_delete", None):
            await super().on_model_delete(model, request)


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
    placeholder_en = StringField("Placeholder - English", render_kw={"class": "form-control"})
    placeholder_ar = StringField("Placeholder - Arabic", render_kw={"class": "form-control"})
    help_text_en = StringField("Help Text - English", render_kw={"class": "form-control"})
    help_text_ar = StringField("Help Text - Arabic", render_kw={"class": "form-control"})
    select_options_raw = HiddenField("Select Options JSON", default="[]", render_kw={"id": "select_options_raw"})

    def process(self, formdata=None, obj=None, data=None, **kwargs):
        if obj and not formdata:
            if getattr(obj, "label_i18n", None):
                kwargs['label_en'] = obj.label_i18n.get('en', '')
                kwargs['label_ar'] = obj.label_i18n.get('ar', '')
            if getattr(obj, "placeholder_i18n", None):
                kwargs['placeholder_en'] = obj.placeholder_i18n.get('en', '')
                kwargs['placeholder_ar'] = obj.placeholder_i18n.get('ar', '')
            if getattr(obj, "help_text_i18n", None):
                kwargs['help_text_en'] = obj.help_text_i18n.get('en', '')
                kwargs['help_text_ar'] = obj.help_text_i18n.get('ar', '')
            if getattr(obj, "select_options_i18n", None):
                arr = [{"key": k, "en": v.get("en", ""), "ar": v.get("ar", "")} for k, v in obj.select_options_i18n.items()]
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
                if opt.get("ar", "").strip():
                    translations["ar"] = opt.get("ar").strip()
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

        def _update_i18n(attr_name, en_val, ar_val):
            existing = dict(getattr(model, attr_name, {}) or {})
            if en_val:
                existing["en"] = en_val
            elif "en" in existing and not en_val:
                del existing["en"]
            if ar_val:
                existing["ar"] = ar_val
            elif "ar" in existing and not ar_val:
                del existing["ar"]
            setattr(model, attr_name, existing if existing else None)

        _update_i18n("label_i18n", data.pop("label_en", None), data.pop("label_ar", None))
        _update_i18n("placeholder_i18n", data.pop("placeholder_en", None), data.pop("placeholder_ar", None))
        _update_i18n("help_text_i18n", data.pop("help_text_en", None), data.pop("help_text_ar", None))

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
    column_list = [Reviews.id, Reviews.user_id, Reviews.item_name,
                   Reviews.rating, Reviews.text, Reviews.created_at]
    column_searchable_list = [Reviews.user_id, Reviews.item_name]
    column_sortable_list = [Reviews.id, Reviews.rating, Reviews.created_at]
    column_default_sort = (Reviews.id, True)
    name = "Review"
    name_plural = "Reviews"
    icon = "fa-solid fa-star"


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
        CheckoutIntakeDraft.id, CheckoutIntakeDraft.user_id, CheckoutIntakeDraft.goods_id,
        CheckoutIntakeDraft.quantity, CheckoutIntakeDraft.status,
        CheckoutIntakeDraft.current_step, CheckoutIntakeDraft.created_at, CheckoutIntakeDraft.expires_at
    ]
    column_searchable_list = [CheckoutIntakeDraft.user_id, CheckoutIntakeDraft.goods_id]
    column_sortable_list = [CheckoutIntakeDraft.id, CheckoutIntakeDraft.created_at]
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    column_details_exclude_list = ["encrypted_payload", "public_token", "schema_fingerprint"]
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


# App Factory
def create_admin_app() -> Starlette:

    from bot.web.export import export_routes

    routes = [
        Route("/health", health_check),
        Route("/metrics", metrics_json),
        Route("/metrics/prometheus", prometheus_metrics),
    ] + export_routes

    app = Starlette(routes=routes)
    app.add_middleware(SessionMiddleware, secret_key=EnvKeys.SECRET_KEY, max_age=1800)

    auth_backend = AdminAuth(secret_key=EnvKeys.SECRET_KEY)
    admin = Admin(
        app,
        engine=Database().engine,
        authentication_backend=auth_backend,
        title="Telegram Shop Admin",
        templates_dir="bot/web/templates",
    )

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
