# Use the official Python image from the Docker Hub
FROM python:3.12

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . .

# Update and Upgrade packages
RUN apt -y update && apt -y upgrade

# Install Unrar
RUN apt -y install unrar-free

RUN apt -y install unar

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

RUN pwd

RUN rm -fr logs && rm -fr comics

RUN mkdir logs && mkdir comics

RUN chmod 755 */

RUN ls -althr

EXPOSE 8000

CMD ["python", "entrypoint.py"]
