import sqladmin
from sqladmin import Admin
from starlette.applications import Starlette

app = Starlette()
admin = Admin(app, engine="sqlite://", templates_dir="bot/web/templates")
print(admin.templates.env.loader)
