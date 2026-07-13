import json
from typing import Any
from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse
from sqlalchemy import select, func

from bot.database.main import Database
from bot.database.models.main import Goods, ProductCustomerField
from bot.misc.customer_field_templates import get_field_templates

class QuickFieldSetView(BaseView):
    name = "Quick Field Set"
    icon = "fa-solid fa-bolt"
    def is_accessible(self, request: Request) -> bool:
        return request.session.get("authenticated", False)

    def is_visible(self, request: Request) -> bool:
        return self.is_accessible(request)

    @expose("/quick-field-set", methods=["GET"])
    async def quick_field_set_get(self, request: Request):

        async with Database().session() as session:
            result = await session.execute(
                select(Goods).where(Goods.fulfillment_mode == "manual").order_by(Goods.name)
            )
            products = result.scalars().all()
            
        templates = get_field_templates()
        
        error = request.session.pop("quick_field_set_error", None)
        success_messages = request.session.pop("quick_field_set_success", None)
        
        return await self.templates.TemplateResponse(
            request, 
            "admin/quick_field_set.html", 
            {
                "products": products, 
                "templates_json": json.dumps(templates),
                "error": error,
                "success_messages": success_messages
            }
        )

    @expose("/quick-field-set", methods=["POST"])
    async def quick_field_set_post(self, request: Request):

        form = await request.form()
        product_id = form.get("product_id")
        template_name = form.get("template")
        scope = form.get("scope")
        duplicate_handling = form.get("duplicate_handling")
        
        try:
            start_sort = int(form.get("sort_order", 0))
            if start_sort < 0:
                request.session["quick_field_set_error"] = "Sort order must be >= 0."
                return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)
        except ValueError:
            request.session["quick_field_set_error"] = "Invalid sort order."
            return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)
            
        templates = get_field_templates()
        if template_name not in templates:
            request.session["quick_field_set_error"] = "Invalid template selected."
            return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)
            
        if scope not in ("per_unit", "per_order"):
            request.session["quick_field_set_error"] = "Invalid scope."
            return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)

        if duplicate_handling not in ("strict", "missing_only"):
            request.session["quick_field_set_error"] = "Invalid duplicate handling mode."
            return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)

        try:
            product_id_int = int(product_id)
        except (ValueError, TypeError):
            request.session["quick_field_set_error"] = "Invalid product ID."
            return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)

        async with Database().session() as session:
            goods = await session.get(Goods, product_id_int)
            if not goods or goods.fulfillment_mode != "manual":
                request.session["quick_field_set_error"] = "Selected product is not a valid manual fulfillment product."
                return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)
            
            existing_result = await session.execute(
                select(ProductCustomerField.field_key).where(ProductCustomerField.goods_id == product_id_int)
            )
            existing_keys = {row[0] for row in existing_result.all()}
            
            new_fields = []
            skipped_keys = []
            created_keys = []
            
            for index, field_def in enumerate(templates[template_name]):
                key = field_def["field_key"]
                if key in existing_keys:
                    if duplicate_handling == "strict":
                        request.session["quick_field_set_error"] = f"Conflict: Field key '{key}' already exists for this product. No fields were created."
                        return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)
                    else:
                        skipped_keys.append(key)
                        continue
                        
                new_field = ProductCustomerField(
                    goods_id=goods.id,
                    field_key=key,
                    field_type=field_def["field_type"],
                    label_i18n=field_def["label_i18n"],
                    placeholder_i18n=field_def.get("placeholder_i18n"),
                    help_text_i18n=field_def.get("help_text_i18n"),
                    required=field_def["required"],
                    is_sensitive=field_def["is_sensitive"],
                    scope=scope,
                    sort_order=start_sort + index,
                    is_active=field_def["is_active"],
                    max_length=field_def.get("max_length")
                )
                
                # Enforce secret fields are always sensitive
                if new_field.field_type == "secret":
                    new_field.is_sensitive = True
                    
                new_fields.append(new_field)
                created_keys.append(key)
                
            if not new_fields and not skipped_keys:
                request.session["quick_field_set_error"] = "Template empty or no action taken."
                return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)
                
            if not new_fields:
                request.session["quick_field_set_success"] = ["Skipped existing: " + ", ".join(skipped_keys)]
                return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)
            
            # Atomic commit
            session.add_all(new_fields)
            await session.commit()
            
        success_msgs = []
        if created_keys:
            success_msgs.append("Created: " + ", ".join(created_keys))
        if skipped_keys:
            success_msgs.append("Skipped existing: " + ", ".join(skipped_keys))
            
        request.session["quick_field_set_success"] = success_msgs
        return RedirectResponse(request.url_for("admin:quick_field_set_get"), status_code=303)

    @expose("/quick-field-set/next-sort-order", methods=["GET"])
    async def quick_field_set_next_sort_order(self, request: Request):
        try:
            product_id = int(request.query_params.get("product_id"))
        except (ValueError, TypeError):
            return JSONResponse({"error": "Invalid product ID"}, status_code=400)
            
        async with Database().session() as session:
            goods = await session.get(Goods, product_id)
            if not goods or goods.fulfillment_mode != "manual":
                return JSONResponse({"error": "Invalid product"}, status_code=400)
                
            result = await session.execute(
                select(func.max(ProductCustomerField.sort_order)).where(ProductCustomerField.goods_id == product_id)
            )
            max_order = result.scalar()
            
        next_order = (max_order + 1) if max_order is not None else 0
        return JSONResponse({"next_sort_order": next_order})
