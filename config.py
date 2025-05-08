#config.py
import os
from dotenv import load_dotenv


basedir = os.path.abspath(os.path.dirname(__file__))
env_path = os.path.join(basedir, '.env')

if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
else:
    print(f"WARNING: .env file not found at {env_path}. Using default or environment-set variables.")

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess-this-super-secret-key'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'classpulse.db')
    
    LOGIN_MESSAGE = "Пожалуйста, войдите, чтобы получить доступ к этой странице."
    LOGIN_MESSAGE_CATEGORY = "warning"

    SERVER_NAME = os.environ.get('SERVER_NAME')
    PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL')

    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SAMESITE = 'Lax'

    # Определяем, работаем ли мы через HTTPS на основе PUBLIC_BASE_URL
    # Это нужно для установки флага Secure для кук
    IS_HTTPS = PUBLIC_BASE_URL and PUBLIC_BASE_URL.startswith('https://')
    if IS_HTTPS:
        SESSION_COOKIE_SECURE = True
        REMEMBER_COOKIE_SECURE = True
    else:
        SESSION_COOKIE_SECURE = False
        REMEMBER_COOKIE_SECURE = False


    DEFAULT_ADMIN_USER = 'admin'
    DEFAULT_ADMIN_PASS = 'adminadmin'

    @staticmethod
    def init_app(app):
        if app.config['SECRET_KEY'] == 'you-will-never-guess-this-super-secret-key':
            app.logger.critical(
                "SECURITY WARNING: Default SECRET_KEY is used. "
                "Set a strong SECRET_KEY environment variable!"
            )

        if not os.environ.get('ADMIN_PASS') and app.config['DEFAULT_ADMIN_PASS'] == 'adminadmin':
            app.logger.critical(
                "SECURITY WARNING: ADMIN_PASS environment variable is not set, "
                "and default password for admin is being used. CHANGE THIS!"
            )

        app.logger.info(f"Application IS_HTTPS: {app.config['IS_HTTPS']}")
        app.logger.info(f"Session Cookie Secure: {app.config['SESSION_COOKIE_SECURE']}")
        if app.config['SERVER_NAME']:
            app.logger.info(f"Configured SERVER_NAME: {app.config['SERVER_NAME']}")
            cookie_domain = app.config['SERVER_NAME'].split(':')[0]
            if cookie_domain != 'localhost' and '.' in cookie_domain:
                app.config['SESSION_COOKIE_DOMAIN'] = f".{cookie_domain}"
                app.config['REMEMBER_COOKIE_DOMAIN'] = f".{cookie_domain}"
                app.logger.info(f"SESSION_COOKIE_DOMAIN set to: {app.config['SESSION_COOKIE_DOMAIN']}")
        else:
            app.logger.warning(
                "SERVER_NAME environment variable not set. "
                "Session cookies might not work correctly across subdomains or behind proxies."
            )
        
        if not app.config['IS_HTTPS'] and app.config['SERVER_NAME']:
            app.logger.warning(
                "ProxyFix might be enabled (implied by SERVER_NAME), but HTTPS not detected via PUBLIC_BASE_URL. "
                "Session cookies will not be Secure. Ensure your proxy handles HTTPS."
            )


class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = 'test-secret-key'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}