import sqladmin
from sqladmin import Admin
from starlette.applications import Starlette

app = Starlette()
admin = Admin(app, engine="sqlite://", templates_dir="bot/web/templates")
for loader in admin.templates.env.loader.loaders:
    print(loader)
