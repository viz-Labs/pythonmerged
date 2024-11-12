import os
import logging
import pandas as pd
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

# Configure logging for SQLAlchemy
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# Ensure the upload folder exists
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'csv'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    """Check if the file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def map_dtype_to_mysql(dtype):
    """Map pandas data types to MySQL data types."""
    if pd.api.types.is_integer_dtype(dtype):
        return 'INT'
    elif pd.api.types.is_float_dtype(dtype):
        return 'FLOAT'
    elif pd.api.types.is_bool_dtype(dtype):
        return 'BOOLEAN'
    elif pd.api.types.is_string_dtype(dtype):
        return 'TEXT'
    elif pd.api.types.is_datetime64_any_dtype(dtype):
        return 'DATETIME'
    else:
        return 'TEXT'

def is_date_column(column):
    """Check if a column is a date column."""
    try:
        pd.to_datetime(column, errors='raise')
        return True
    except:
        return False

def convert_to_datetime(column):
    """Convert column to datetime."""
    return pd.to_datetime(column, errors='coerce')

@csrf_exempt
def upload_file(request):
    """Handle file upload and save to MySQL database."""
    if request.method == 'POST':
        # Get database connection details from request
        host = request.POST.get('host')
        user = request.POST.get('user')
        password = request.POST.get('password')
        database = request.POST.get('database')

        # Check if all database connection details are provided
        if not all([host, user, password, database]):
            return JsonResponse({'error': 'Missing database connection details'}, status=400)

        # Ensure file and table name are provided
        if 'file' not in request.FILES or 'table_name' not in request.POST:
            return JsonResponse({'error': 'No file or table name provided'}, status=400)

        table_name = request.POST['table_name'].strip()
        if not table_name:
            return JsonResponse({'error': 'Table name cannot be empty'}, status=400)

        file = request.FILES['file']
        if file.name == '':
            return JsonResponse({'error': 'No selected file'}, status=400)

        # Check if the file type is allowed
        if file and allowed_file(file.name):
            filename = secure_filename(file.name)
            filepath = os.path.join(UPLOAD_FOLDER, filename)

            try:
                # Save the file to the server
                path = default_storage.save(filepath, ContentFile(file.read()))
                print("Step: File saved successfully")
            except Exception as e:
                return JsonResponse({'error': 'Error saving file', 'message': str(e)}, status=500)

            try:
                # Load CSV data into a pandas DataFrame
                data = pd.read_csv(filepath, low_memory=False)
                print("Step: CSV data loaded")
                if data.empty:
                    return JsonResponse({'error': 'Uploaded file is empty'}, status=400)
            except pd.errors.ParserError as e:
                return JsonResponse({'error': 'Error parsing CSV file', 'message': str(e)}, status=500)
            except Exception as e:
                return JsonResponse({'error': 'Error reading file', 'message': str(e)}, status=500)

            try:
                # Data processing: Convert date columns and optimize data types
                for col in data.columns:
                    if is_date_column(data[col]):
                        data[col] = convert_to_datetime(data[col])

                for col in data.columns:
                    if not is_date_column(data[col]):
                        if pd.api.types.is_integer_dtype(data[col]):
                            data[col] = pd.to_numeric(data[col], downcast='integer')
                        elif pd.api.types.is_float_dtype(data[col]):
                            data[col] = pd.to_numeric(data[col], downcast='float')

                # Create SQLAlchemy engine for MySQL connection
                engine = create_engine(f'mysql+pymysql://{user}:{password}@{host}/{database}')

                # Save data to MySQL table (replace if table already exists)
                data.to_sql(table_name, engine, if_exists='replace', index=False)
                print("Step: Data saved to MySQL")

                # Return success response
                return JsonResponse({'message': f'File uploaded and data saved to table "{table_name}" successfully!'})

            except SQLAlchemyError as e:
                return JsonResponse({'error': 'Database error', 'message': str(e)}, status=500)
            except Exception as e:
                return JsonResponse({'error': 'Error saving data to database', 'message': str(e)}, status=500)

        return JsonResponse({'error': 'Invalid file type'}, status=400)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)