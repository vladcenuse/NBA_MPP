# NBA Stats API Backend

This is the backend for the NBA Stats application.

## Deployment on Render

### Prerequisites
- A [Render](https://render.com) account
- Git repository with this code

### Deployment Steps

1. Log in to your Render account
2. Click on "New +" and select "Web Service"
3. Connect your repository or use "Deploy from GitHub"
4. Configure the service:
   - Name: nba-stats-api (or your preferred name)
   - Environment: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn main:app -k uvicorn.workers.UvicornWorker --timeout 120`
5. Select the appropriate plan (Free tier works for development)
6. Click "Create Web Service"

### Database Considerations

This application uses SQLite which is stored in the file system. On Render:
- The database will be created in the service's file system
- Data will persist as long as the disk is attached
- For production, consider using a managed database service

### Environment Variables

No specific environment variables are required for the basic setup. The application will create and use the SQLite database file in the application directory.

## Local Development

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Run the application:
   ```
   uvicorn main:app --reload
   ```

3. Access the API at http://localhost:8000

## API Documentation

Once deployed, API documentation is available at:
- Swagger UI: https://your-app-url/docs
- ReDoc: https://your-app-url/redoc 