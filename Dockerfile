# Gets a lightweight version for Python
FROM python:3.12-slim

# Creates a directory and moves to it
WORKDIR /app

# Copy the source code from your current working directory to the newly created directory inside the container
ADD . /app

# Install all the dependencies
RUN pip install -r requirements.txt

# Install the GStreamer to convert from webm to PCM (important to install, otherwise won't work)
RUN apt-get update && apt-get install -y libgstreamer1.0-0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly

# Expose the port 8000, it's commonly used by FastAPI
EXPOSE 8000

# Runs the FastAPI application for production
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]