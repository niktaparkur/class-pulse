# app/director/__init__.py

from flask import Blueprint
from flask_login import LoginManager

# Не импортируем db и модели здесь

director_bp = Blueprint(
    "director", __name__, template_folder="templates", url_prefix="/director"
)

# Создаем LoginManager ЗДЕСЬ
dir_login_manager = LoginManager()
dir_login_manager.login_view = "director.login_route"
dir_login_manager.login_message = "Доступ только для Директоров. Пожалуйста, войдите."
dir_login_manager.login_message_category = "warning"
dir_login_manager.session_protection = "strong"
dir_login_manager.session_cookie_name = "_dir_user_id"

# !!! Инициализируем менеджер с БЛЮПРИНТОМ !!!
dir_login_manager.init_app(director_bp)


# Определяем user_loader для этого менеджера
@dir_login_manager.user_loader
def load_director(user_id):
    from ..models import db, Director  # Импортируем здесь

    # current_app.logger.debug(f"--- director_loader: Loading Director with ID: {user_id} ---")
    try:
        return db.session.get(Director, int(user_id))
    except ValueError:
        return None


# Импортируем views ПОСЛЕ определения bp и менеджера
from . import views
