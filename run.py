# run.py
import eventlet
eventlet.monkey_patch()

# It's crucial that monkey_patch() is called BEFORE the app and socketio are imported.
from app import app, socketio

if __name__ == '__main__':
    # Note: This part is for local development and will not be used by Gunicorn
    # on Render, but it's good practice to have it.
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)