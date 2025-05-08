from flask import Blueprint

# url_prefix важен, чтобы маршруты суперадмина не пересекались с учительскими
superadmin_bp = Blueprint(
    "superadmin", __name__, template_folder="templates", url_prefix="/superadmin"
)

# Отдельный LoginManager для суперадмина, если ему нужен вход через Flask-Login
from flask_login import LoginManager

sa_login_manager = LoginManager()
sa_login_manager.login_view = (
    "superadmin.login_route"  # Маршрут логина для этого блюпринта
)
sa_login_manager.login_message = "Доступ только для Суперадминов. Пожалуйста, войдите."
sa_login_manager.login_message_category = "warning"

from . import views  # Импортируем views после создания bp и sa_login_manager
