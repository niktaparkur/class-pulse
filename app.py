#app.py раннее run.py
import os
from flask import url_for
from app import create_app
from app.core.views import setup_database_and_admin

app, socketio = create_app()


if __name__ == "__main__":
    try:
        setup_database_and_admin(app)
        app.logger.info("Database and admin setup complete (or checked).")
    except Exception as e:
        app.logger.critical(f"CRITICAL: Failed to setup database and admin: {e}")

    flask_debug_str = os.environ.get("FLASK_DEBUG", "False").lower()
    flask_debug = flask_debug_str in ("true", "1", "t", "yes")

    port = int(os.environ.get("PORT", 5001))
    host = os.environ.get("HOST", "0.0.0.0")

    app.logger.info(f"Preparing to run SocketIO server...")
    app.logger.info(f"  - Host: {host}")
    app.logger.info(f"  - Port: {port}")
    app.logger.info(f"  - Flask Debug Mode: {flask_debug}")

    app.logger.info(f"  - SocketIO Async Mode: {socketio.async_mode}")

    with app.app_context():
        app.logger.info(
            f"  - Public Base URL (for links): {app.config.get('PUBLIC_BASE_URL', 'Not Set')}"
        )
        app.logger.info(
            f"  - Server Name (for cookies): {app.config.get('SERVER_NAME', 'Not Set')}"
        )

        try:
            core_static_url = url_for("core.static", filename="test.file")
            app.logger.info(
                f"  - Example URL for 'core.static': {core_static_url.rsplit('/',1)[0]}/"
            )
        except Exception as e:
            app.logger.warning(
                f"  - Could not generate example URL for 'core.static': {e}"
            )

    socketio.run(app, host=host, port=port, debug=flask_debug, use_reloader=flask_debug)
