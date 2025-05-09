from flask import Blueprint
from flask_login import LoginManager

# Не импортируем db и модели здесь, если они не нужны прямо в __init__

superadmin_bp = Blueprint(
    "superadmin", __name__, template_folder="templates", url_prefix="/superadmin"
)

# Создаем LoginManager ЗДЕСЬ
sa_login_manager = LoginManager()
sa_login_manager.login_view = "superadmin.login_route"
sa_login_manager.login_message = "Доступ только для Суперадминов. Пожалуйста, войдите."
sa_login_manager.login_message_category = "warning"
sa_login_manager.session_protection = "strong"  # Рекомендуется
sa_login_manager.session_cookie_name = "_sa_user_id"

# !!! Инициализируем менеджер с БЛЮПРИНТОМ !!!
sa_login_manager.init_app(superadmin_bp)


# Определяем user_loader для этого менеджера
@sa_login_manager.user_loader
def load_superadmin(user_id):
    from ..models import (
        db,
        SuperAdmin,
    )  # Импортируем здесь, чтобы избежать циклических зависимостей

    # current_app.logger.debug(f"--- superadmin_loader: Loading SA with ID: {user_id} ---")
    try:
        return db.session.get(SuperAdmin, int(user_id))
    except ValueError:
        return None


# Импортируем views ПОСЛЕ определения bp и менеджера
from . import views
