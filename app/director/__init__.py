from flask import Blueprint

director_bp = Blueprint(
    "director", __name__, template_folder="templates", url_prefix="/director"
)

from flask_login import LoginManager

dir_login_manager = LoginManager()
dir_login_manager.login_view = (
    "director.login_route"  # Маршрут логина для этого блюпринта
)
dir_login_manager.login_message = "Доступ только для Директоров. Пожалуйста, войдите."
dir_login_manager.login_message_category = "warning"

from . import views
