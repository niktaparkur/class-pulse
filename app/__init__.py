import os
import logging
from logging.config import dictConfig

from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config as app_config

from .models import db, User

from .utils import nl2br_filter


login_manager = LoginManager()
socketio = SocketIO()

dictConfig(
    {
        "version": 1,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
            }
        },
        "handlers": {
            "wsgi": {
                "class": "logging.StreamHandler",
                "stream": "ext://flask.logging.wsgi_errors_stream",
                "formatter": "default",
            }
        },
        "root": {"level": "INFO", "handlers": ["wsgi"]},
    }
)


@login_manager.user_loader
def load_user(user_id):
    from flask import current_app

    current_app.logger.debug(f"--- user_loader: Loading user with ID: {user_id} ---")
    try:
        user = db.session.get(User, int(user_id))
        if user:
            current_app.logger.debug(
                f"--- user_loader: User {user_id} ({user.username}) found. ---"
            )
        else:
            current_app.logger.warning(
                f"--- user_loader: User {user_id} NOT FOUND in DB! ---"
            )
        return user
    except ValueError:
        current_app.logger.error(
            f"--- user_loader: Invalid user_id format: {user_id} ---"
        )
        return None
    except Exception as e:
        current_app.logger.error(
            f"--- user_loader: Error loading user {user_id}: {e} ---"
        )
        db.session.rollback()
        return None


def create_app(config_name=None):
    """
    Фабрика приложений Flask.
    Создает и конфигурирует экземпляр приложения Flask.
    """
    if config_name is None:
        config_name = os.environ.get("FLASK_CONFIG", "default")

    app = Flask(__name__, instance_relative_config=False)

    selected_config_name = os.environ.get("FLASK_CONFIG", "default")
    current_config_object = app_config.get(selected_config_name)
    if not current_config_object:
        app.logger.critical(
            f"Warning: Config '{selected_config_name}' not found. Falling back to 'default'."
        )
        current_config_object = app_config["default"]

    app.config.from_object(current_config_object)
    app.logger.info(f"Application configured with '{selected_config_name}' settings.")

    if hasattr(current_config_object, "init_app"):
        current_config_object.init_app(app)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.logger.info("ProxyFix applied to wsgi_app.")

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "core.login_route"
    login_manager.login_message = app.config.get(
        "LOGIN_MESSAGE", "Пожалуйста, войдите, чтобы получить доступ к этой странице."
    )
    login_manager.login_message_category = app.config.get(
        "LOGIN_MESSAGE_CATEGORY", "warning"
    )

    async_mode = None
    try:
        import eventlet

        async_mode = "eventlet"
        app.logger.info("Attempting to use eventlet for SocketIO async mode.")
    except ImportError:
        app.logger.info(
            "Eventlet not found, SocketIO will use default async mode (threading/werkzeug)."
        )

    app.config["SOCKETIO_ASYNC_MODE"] = async_mode
    socketio.init_app(app, async_mode=async_mode)
    app.logger.info(f"SocketIO initialized with async_mode: {async_mode}.")

    app.jinja_env.filters["nl2br"] = nl2br_filter
    app.logger.info("Jinja2 filter 'nl2br' registered.")

    from .core import core_bp as core_blueprint

    app.register_blueprint(core_blueprint)
    app.logger.info("Blueprint 'core' registered.")

    @app.errorhandler(404)
    def page_not_found_error(err):
        # current_app.logger.error(f"Page not found: {request.url} - {err}")
        return (
            render_template(
                "error.html",
                title="Страница не найдена (404)",
                message="Запрашиваемая страница не существует.",
                heading="Упс! Страница потерялась",
            ),
            404,
        )


    @app.errorhandler(500)
    def internal_server_error(err):
        # current_app.logger.error(f"Internal server error: {err}", exc_info=True)
        # db.session.rollback() # Важно откатить сессию БД при внутренней ошибке
        return (
            render_template(
                "error.html",
                title="Внутренняя ошибка (500)",
                message="На сервере произошла непредвиденная ошибка. Мы уже работаем над этим!",
                heading="Ой! Что-то сломалось",
            ),
            500,
        )


    return app, socketio


