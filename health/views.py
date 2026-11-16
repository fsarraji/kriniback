from django.db import connection
from django.db.utils import OperationalError
from django.http import JsonResponse


def health_check(request):
    """Health check endpoint to verify service and database connectivity."""
    health_status = {
        "status": "ok",
        "service": "krini-backend",
        "database": "unknown",
    }

    # Check database connectivity
    try:
        # Execute a simple query to check if the database is responsive
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
            if row is not None and row[0] == 1:
                health_status["database"] = "ok"
            else:
                health_status["database"] = "error"
                health_status["status"] = "error"
    except OperationalError as e:
        health_status["database"] = "error"
        health_status["status"] = "error"
        health_status["error"] = str(e)

    status_code = 200 if health_status["status"] == "ok" else 503
    return JsonResponse(health_status, status=status_code)
