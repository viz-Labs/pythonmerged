import os
import re
import pandas as pd
import pymysql
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.storage import default_storage
from django.core.exceptions import SuspiciousFileOperation

UPLOADS_PATH = os.path.join(settings.MEDIA_ROOT, 'uploads')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'csv'}

def sanitize_filename(filename):
    # Use os.path.basename to get the base filename, which strips out any directory information
    basename = os.path.basename(filename)
    
    # Check for path traversal
    if '..' in basename or basename.startswith('/'):
        raise SuspiciousFileOperation("Detected path traversal attempt in filename")
    
    return basename

def map_dtype_to_mysql(dtype):
    if pd.api.types.is_integer_dtype(dtype):
        return 'INT'
    elif pd.api.types.is_float_dtype(dtype):
        return 'FLOAT'
    elif pd.api.types.is_bool_dtype(dtype):
        return 'BOOLEAN'
    elif pd.api.types.is_string_dtype(dtype):
        return 'VARCHAR(255)'
    elif pd.api.types.is_datetime64_any_dtype(dtype):
        return 'DATETIME'
    else:
        return 'VARCHAR(255)'

def is_date_column(series):
    date_patterns = [
        re.compile(r'^\d{4}-\d{2}-\d{2}$'),  # YYYY-MM-DD
        re.compile(r'^\d{4}/\d{2}/\d{2}$'),  # YYYY/MM/DD
        re.compile(r'^\d{2}/\d{2}/\d{4}$'),  # MM/DD/YYYY
        re.compile(r'^\d{2}-\d{2}-\d{4}$'),  # MM-DD-YYYY
        re.compile(r'^\d{2}/\d{2}/\d{2}$'),  # MM/DD/YY
        re.compile(r'^\d{2}-\d{2}-\d{2}$'),  # MM-DD-YY
        re.compile(r'^\d{2}/\d{2}/\d{2}$'),  # DD/MM/YY
        re.compile(r'^\d{2}/\d{2}/\d{4}$'),  # DD/MM/YYYY
        re.compile(r'^\d{2}-\d{2}-\d{4}$'),  # DD-MM-YYYY
        re.compile(r'^\d{4}\d{2}\d{2}$'),    # YYYYMMDD
        re.compile(r'^\d{2}\d{2}\d{4}$'),    # DDMMYYYY
        re.compile(r'^\d{2}\d{2}\d{2}$'),    # DDMMYY
    ]
    
    sample_values = series.dropna().astype(str).head(100)
    if all(any(pattern.match(value) for pattern in date_patterns) for value in sample_values):
        return True
    return False

def get_date_format(series):
    known_formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y",
        "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
        "%Y%m%d", "%d%m%Y", "%d%m%y"
    ]
    
    date_format_counts = {}
    
    for value in series.dropna().astype(str):
        for fmt in known_formats:
            try:
                pd.to_datetime(value, format=fmt, errors='raise')
                date_format_counts[fmt] = date_format_counts.get(fmt, 0) + 1
                break
            except ValueError:
                continue
    
    if date_format_counts:
        return max(date_format_counts, key=date_format_counts.get)
    return None

def convert_to_datetime(series):
    format_detected = get_date_format(series)
    if format_detected:
        return pd.to_datetime(series, format=format_detected, errors='coerce')
    return pd.to_datetime(series, infer_datetime_format=True, errors='coerce')

def has_mixed_types(series):
    types = series.dropna().apply(lambda x: type(x)).unique()
    return len(types) > 1

def clean_data_for_mysql(data):
    for col in data.columns:
        if data[col].dtype == 'datetime64[ns]':
            data[col] = data[col].apply(lambda x: x if pd.notnull(x) else None)
        else:
            data[col] = data[col].apply(lambda x: x if pd.notnull(x) else None)
    return data

@csrf_exempt
def upload_file(request):
    print("req")
    if request.method == 'POST':
        host = request.POST.get('host')
        user = request.POST.get('user')
        password = request.POST.get('password')
        database = request.POST.get('database')

        if not all([host, user, password, database]):
            return JsonResponse({'error': 'Missing database connection details'}, status=400)

        if 'file' not in request.FILES or 'table_name' not in request.POST:
            return JsonResponse({'error': 'No file or table name provided'}, status=400)

        table_name = request.POST['table_name'].strip()
        if not table_name:
            return JsonResponse({'error': 'Table name cannot be empty'}, status=400)

        file = request.FILES['file']
        if file.name == '':
            return JsonResponse({'error': 'No selected file'}, status=400)

        if file and allowed_file(file.name):
            try:
                # Sanitize the filename to prevent path traversal
                sanitized_filename = sanitize_filename(file.name)

                # Save the file to the desired location
                filename = default_storage.save(os.path.join(UPLOADS_PATH, sanitized_filename), file)
                
                # Construct the correct file path
                filepath = os.path.join(settings.MEDIA_ROOT, filename)
                
                data = pd.read_csv(filepath, low_memory=False)
                if data.empty:
                    return JsonResponse({'error': 'Uploaded file is empty'}, status=400)
            except SuspiciousFileOperation as e:
                return JsonResponse({'error': str(e)}, status=400)
            except pd.errors.ParserError as e:
                return JsonResponse({'error': 'Error parsing CSV file', 'message': str(e)}, status=500)
            except Exception as e:
                return JsonResponse({'error': 'Error reading file', 'message': str(e)}, status=500)

            try:
                # Drop rows that are completely empty
                data.dropna(how='all', inplace=True)

                # Data processing
                for col in data.columns:
                    if is_date_column(data[col]):
                        data[col] = convert_to_datetime(data[col])

                for col in data.columns:
                    if not is_date_column(data[col]):
                        if has_mixed_types(data[col]):
                            data[col] = data[col].astype(str)
                        elif pd.api.types.is_integer_dtype(data[col]):
                            data[col] = pd.to_numeric(data[col], downcast='integer')
                        elif pd.api.types.is_float_dtype(data[col]):
                            data[col] = pd.to_numeric(data[col], downcast='float')
                        elif pd.api.types.is_string_dtype(data[col]):
                            data[col] = data[col].astype('string')

                # Clean the data for MySQL compatibility
                data = clean_data_for_mysql(data)

            except Exception as e:
                return JsonResponse({'error': 'Error processing data', 'message': str(e)}, status=500)

            try:
                # Establish database connection
                connection = pymysql.connect(
                    host=host,
                    user=user,
                    password=password,
                    database=database,
                    cursorclass=pymysql.cursors.DictCursor
                )
                
                with connection.cursor() as cursor:
                    # Drop the table if it exists
                    cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
                    
                    # Create the table
                    columns = ', '.join(f"`{col}` {map_dtype_to_mysql(data[col].dtype)}" for col in data.columns)
                    create_table_query = f"CREATE TABLE `{table_name}` ({columns})"
                    cursor.execute(create_table_query)
                    
                    # Insert data in chunks
                    chunk_size = 1000
                    for start in range(0, len(data), chunk_size):
                        chunk = data.iloc[start:start + chunk_size]
                        values = ', '.join(str(tuple(x)) for x in chunk.values)
                        values = values.replace('NaT', 'NULL').replace('nan', 'NULL')  # Handle NaT and nan values
                        insert_query = f"INSERT INTO `{table_name}` VALUES {values}"
                        cursor.execute(insert_query)
                        
                connection.commit()
                connection.close()
            except pymysql.MySQLError as e:
                return JsonResponse({'error': 'Database error', 'message': str(e)}, status=500)
            except Exception as e:
                return JsonResponse({'error': 'Error saving data to MySQL', 'message': str(e)}, status=500)

            return JsonResponse({'message': 'File uploaded and data inserted successfully'})
        else:
            return JsonResponse({'error': 'Invalid file type. Only CSV files are allowed.'}, status=400)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)

# # views.py
# import os
# import re
# import pandas as pd
# import pymysql
# from django.conf import settings
# from django.http import JsonResponse
# from django.views.decorators.csrf import csrf_exempt
# from django.core.files.storage import default_storage

# def allowed_file(filename):
#     return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'csv'}

# def map_dtype_to_mysql(dtype):
#     if pd.api.types.is_integer_dtype(dtype):
#         return 'INT'
#     elif pd.api.types.is_float_dtype(dtype):
#         return 'FLOAT'
#     elif pd.api.types.is_bool_dtype(dtype):
#         return 'BOOLEAN'
#     elif pd.api.types.is_string_dtype(dtype):
#         return 'VARCHAR(255)'
#     elif pd.api.types.is_datetime64_any_dtype(dtype):
#         return 'DATETIME'
#     else:
#         return 'VARCHAR(255)'

# def is_date_column(series):
#     date_patterns = [
#         re.compile(r'^\d{4}-\d{2}-\d{2}$'),  # YYYY-MM-DD
#         re.compile(r'^\d{4}/\d{2}/\d{2}$'),  # YYYY/MM/DD
#         re.compile(r'^\d{2}/\d{2}/\d{4}$'),  # MM/DD/YYYY
#         re.compile(r'^\d{2}-\d{2}-\d{4}$'),  # MM-DD-YYYY
#         re.compile(r'^\d{2}/\d{2}/\d{2}$'),  # MM/DD/YY
#         re.compile(r'^\d{2}-\d{2}-\d{2}$'),  # MM-DD-YY
#         re.compile(r'^\d{2}/\d{2}/\d{2}$'),  # DD/MM/YY
#         re.compile(r'^\d{2}/\d{2}/\d{4}$'),  # DD/MM/YYYY
#         re.compile(r'^\d{2}-\d{2}-\d{4}$'),  # DD-MM-YYYY
#         re.compile(r'^\d{4}\d{2}\d{2}$'),    # YYYYMMDD
#         re.compile(r'^\d{2}\d{2}\d{4}$'),    # DDMMYYYY
#         re.compile(r'^\d{2}\d{2}\d{2}$'),    # DDMMYY
#     ]
    
#     sample_values = series.dropna().astype(str).head(100)
#     if all(any(pattern.match(value) for pattern in date_patterns) for value in sample_values):
#         return True
#     return False

# def get_date_format(series):
#     known_formats = [
#         "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y",
#         "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
#         "%Y%m%d", "%d%m%Y", "%d%m%y"
#     ]
    
#     date_format_counts = {}
    
#     for value in series.dropna().astype(str):
#         for fmt in known_formats:
#             try:
#                 pd.to_datetime(value, format=fmt, errors='raise')
#                 date_format_counts[fmt] = date_format_counts.get(fmt, 0) + 1
#                 break
#             except ValueError:
#                 continue
    
#     if date_format_counts:
#         return max(date_format_counts, key=date_format_counts.get)
#     return None

# def convert_to_datetime(series):
#     format_detected = get_date_format(series)
#     if format_detected:
#         return pd.to_datetime(series, format=format_detected, errors='coerce')
#     return pd.to_datetime(series, infer_datetime_format=True, errors='coerce')

# def has_mixed_types(series):
#     types = series.dropna().apply(lambda x: type(x)).unique()
#     return len(types) > 1

# def clean_data_for_mysql(data):
#     for col in data.columns:
#         if data[col].dtype == 'datetime64[ns]':
#             data[col] = data[col].apply(lambda x: x if pd.notnull(x) else None)
#         else:
#             data[col] = data[col].apply(lambda x: x if pd.notnull(x) else None)
#     return data

# @csrf_exempt
# def upload_file(request):
#     if request.method == 'POST':
#         host = request.POST.get('host')
#         user = request.POST.get('user')
#         password = request.POST.get('password')
#         database = request.POST.get('database')

#         if not all([host, user, password, database]):
#             return JsonResponse({'error': 'Missing database connection details'}, status=400)

#         if 'file' not in request.FILES or 'table_name' not in request.POST:
#             return JsonResponse({'error': 'No file or table name provided'}, status=400)

#         table_name = request.POST['table_name'].strip()
#         if not table_name:
#             return JsonResponse({'error': 'Table name cannot be empty'}, status=400)

#         file = request.FILES['file']
#         if file.name == '':
#             return JsonResponse({'error': 'No selected file'}, status=400)

#         if file and allowed_file(file.name):
#             filename = default_storage.save(os.path.join(settings.MEDIA_ROOT, file.name), file)
#             filepath = os.path.join(settings.MEDIA_ROOT, filename)
            
#             try:
#                 data = pd.read_csv(filepath, low_memory=False)
#                 if data.empty:
#                     return JsonResponse({'error': 'Uploaded file is empty'}, status=400)
#             except pd.errors.ParserError as e:
#                 return JsonResponse({'error': 'Error parsing CSV file', 'message': str(e)}, status=500)
#             except Exception as e:
#                 return JsonResponse({'error': 'Error reading file', 'message': str(e)}, status=500)

#             try:
#                 # Drop rows that are completely empty
#                 data.dropna(how='all', inplace=True)

#                 # Data processing
#                 for col in data.columns:
#                     if is_date_column(data[col]):
#                         data[col] = convert_to_datetime(data[col])

#                 for col in data.columns:
#                     if not is_date_column(data[col]):
#                         if has_mixed_types(data[col]):
#                             data[col] = data[col].astype(str)
#                         elif pd.api.types.is_integer_dtype(data[col]):
#                             data[col] = pd.to_numeric(data[col], downcast='integer')
#                         elif pd.api.types.is_float_dtype(data[col]):
#                             data[col] = pd.to_numeric(data[col], downcast='float')
#                         elif pd.api.types.is_string_dtype(data[col]):
#                             data[col] = data[col].astype('string')

#                 # Clean the data for MySQL compatibility
#                 data = clean_data_for_mysql(data)

#             except Exception as e:
#                 return JsonResponse({'error': 'Error processing data', 'message': str(e)}, status=500)

#             try:
#                 # Establish database connection
#                 connection = pymysql.connect(
#                     host=host,
#                     user=user,
#                     password=password,
#                     database=database,
#                     cursorclass=pymysql.cursors.DictCursor
#                 )
                
#                 with connection.cursor() as cursor:
#                     # Drop the table if it exists
#                     cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
                    
#                     # Create the table
#                     columns = ', '.join(f"`{col}` {map_dtype_to_mysql(data[col].dtype)}" for col in data.columns)
#                     create_table_query = f"CREATE TABLE `{table_name}` ({columns})"
#                     cursor.execute(create_table_query)
                    
#                     # Insert data in chunks
#                     chunk_size = 1000
#                     for start in range(0, len(data), chunk_size):
#                         chunk = data.iloc[start:start + chunk_size]
#                         values = ', '.join(str(tuple(x)) for x in chunk.values)
#                         values = values.replace('NaT', 'NULL').replace('nan', 'NULL')  # Handle NaT and nan values
#                         insert_query = f"INSERT INTO `{table_name}` VALUES {values}"
#                         cursor.execute(insert_query)
                        
#                 connection.commit()
#                 connection.close()
#             except pymysql.MySQLError as e:
#                 return JsonResponse({'error': 'Error creating table or uploading data', 'message': str(e)}, status=500)

#             return JsonResponse({'message': 'File uploaded and table created successfully'}, status=201)

#         return JsonResponse({'error': 'Invalid file type'}, status=400)

#     return JsonResponse({'error': 'Invalid request method'}, status=405)





# # views.py
# import os
# import re
# import pandas as pd
# from django.conf import settings
# from django.http import JsonResponse
# from django.views.decorators.csrf import csrf_exempt
# from django.core.files.storage import default_storage
# from sqlalchemy import create_engine, text
# from sqlalchemy.exc import SQLAlchemyError

# def allowed_file(filename):
#     return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'csv'}

# def map_dtype_to_mysql(dtype):
#     if pd.api.types.is_integer_dtype(dtype):
#         return 'INT'
#     elif pd.api.types.is_float_dtype(dtype):
#         return 'FLOAT'
#     elif pd.api.types.is_bool_dtype(dtype):
#         return 'BOOLEAN'
#     elif pd.api.types.is_string_dtype(dtype):
#         return 'TEXT'
#     elif pd.api.types.is_datetime64_any_dtype(dtype):
#         return 'DATETIME'
#     else:
#         return 'TEXT'

# def is_date_column(series):
#     date_patterns = [
#         re.compile(r'^\d{4}-\d{2}-\d{2}$'),  # YYYY-MM-DD
#         re.compile(r'^\d{4}/\d{2}/\d{2}$'),  # YYYY/MM/DD
#         re.compile(r'^\d{2}/\d{2}/\d{4}$'),  # MM/DD/YYYY
#         re.compile(r'^\d{2}-\d{2}-\d{4}$'),  # MM-DD-YYYY
#         re.compile(r'^\d{2}/\d{2}/\d{2}$'),  # MM/DD/YY
#         re.compile(r'^\d{2}-\d{2}-\d{2}$'),  # MM-DD-YY
#         re.compile(r'^\d{2}/\d{2}/\d{2}$'),  # DD/MM/YY
#         re.compile(r'^\d{2}/\d{2}/\d{4}$'),  # DD/MM/YYYY
#         re.compile(r'^\d{2}-\d{2}-\d{4}$'),  # DD-MM-YYYY
#         re.compile(r'^\d{4}\d{2}\d{2}$'),    # YYYYMMDD
#         re.compile(r'^\d{2}\d{2}\d{4}$'),    # DDMMYYYY
#         re.compile(r'^\d{2}\d{2}\d{2}$'),    # DDMMYY
#     ]
    
#     sample_values = series.dropna().astype(str).head(100)
#     if all(any(pattern.match(value) for pattern in date_patterns) for value in sample_values):
#         return True
#     return False

# def get_date_format(series):
#     known_formats = [
#         "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m-%d-%Y",
#         "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
#         "%Y%m%d", "%d%m%Y", "%d%m%y"
#     ]
    
#     date_format_counts = {}
    
#     for value in series.dropna().astype(str):
#         for fmt in known_formats:
#             try:
#                 pd.to_datetime(value, format=fmt, errors='raise')
#                 date_format_counts[fmt] = date_format_counts.get(fmt, 0) + 1
#                 break
#             except ValueError:
#                 continue
    
#     if date_format_counts:
#         return max(date_format_counts, key=date_format_counts.get)
#     return None

# def convert_to_datetime(series):
#     format_detected = get_date_format(series)
#     if format_detected:
#         return pd.to_datetime(series, format=format_detected, errors='coerce')
#     return pd.to_datetime(series, infer_datetime_format=True, errors='coerce')

# @csrf_exempt
# def upload_file(request):
#     origin = request.META.get('HTTP_ORIGIN', 'Unknown Origin')
#     print(f"Request Origin: {origin}")

#     print(request)
#     print("hui")
#     if request.method == 'POST':
#         host = request.POST.get('host')
#         user = request.POST.get('user')
#         password = request.POST.get('password')
#         database = request.POST.get('database')

#         if not all([host, user, password, database]):
#             return JsonResponse({'error': 'Missing database connection details'}, status=400)

#         if 'file' not in request.FILES or 'table_name' not in request.POST:
#             return JsonResponse({'error': 'No file or table name provided'}, status=400)

#         table_name = request.POST['table_name'].strip()
#         if not table_name:
#             return JsonResponse({'error': 'Table name cannot be empty'}, status=400)

#         file = request.FILES['file']
#         if file.name == '':
#             return JsonResponse({'error': 'No selected file'}, status=400)

#         if file and allowed_file(file.name):
#             filename = default_storage.save(os.path.join(settings.MEDIA_ROOT, file.name), file)
#             filepath = os.path.join(settings.MEDIA_ROOT, filename)
            
#             try:
#                 data = pd.read_csv(filepath, low_memory=False)
#                 if data.empty:
#                     return JsonResponse({'error': 'Uploaded file is empty'}, status=400)
#             except pd.errors.ParserError as e:
#                 return JsonResponse({'error': 'Error parsing CSV file', 'message': str(e)}, status=500)
#             except Exception as e:
#                 return JsonResponse({'error': 'Error reading file', 'message': str(e)}, status=500)

#             try:
#                 # Data processing
#                 for col in data.columns:
#                     if is_date_column(data[col]):
#                         data[col] = convert_to_datetime(data[col])

#                 for col in data.columns:
#                     if not is_date_column(data[col]):
#                         if pd.api.types.is_integer_dtype(data[col]):
#                             data[col] = pd.to_numeric(data[col], downcast='integer')
#                         elif pd.api.types.is_float_dtype(data[col]):
#                             data[col] = pd.to_numeric(data[col], downcast='float')
#                         elif pd.api.types.is_string_dtype(data[col]):
#                             data[col] = data[col].astype('string')
#             except Exception as e:
#                 return JsonResponse({'error': 'Error processing data', 'message': str(e)}, status=500)

#             try:
#                 # Establish database connection
#                 engine = create_engine(
#                     f'mysql+mysqldb://{user}:{password}@{host}/{database}', 
#                     connect_args={'connect_timeout': 10}
#                 )
#                 columns = ', '.join(f"`{col}` {map_dtype_to_mysql(data[col].dtype)}" for col in data.columns)
#                 create_table_query = f"CREATE TABLE IF NOT EXISTS `{table_name}` ({columns})"
                
#                 with engine.connect() as connection:
#                     with connection.begin():
#                         connection.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))
#                         connection.execute(text(create_table_query))

#                     # Insert data in chunks
#                     data.to_sql(table_name, con=connection, if_exists='append', index=False, method='multi', chunksize=1000)
#             except SQLAlchemyError as e:
#                 return JsonResponse({'error': 'Error creating table or uploading data', 'message': str(e)}, status=500)

#             return JsonResponse({'message': 'File uploaded and table created successfully'}, status=201)

#         return JsonResponse({'error': 'Invalid file type'}, status=400)

#     return JsonResponse({'error': 'Invalid request method'}, status=405)














# import os
# import logging
# import pandas as pd
# from django.http import JsonResponse
# from django.views.decorators.csrf import csrf_exempt
# from django.core.files.storage import default_storage
# from django.core.files.base import ContentFile
# from sqlalchemy import create_engine
# from sqlalchemy.exc import SQLAlchemyError
# from werkzeug.utils import secure_filename

# # Configure logging for SQLAlchemy
# logging.basicConfig()
# logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# # Ensure the upload folder exists
# UPLOAD_FOLDER = 'uploads'
# ALLOWED_EXTENSIONS = {'csv'}

# if not os.path.exists(UPLOAD_FOLDER):
#     os.makedirs(UPLOAD_FOLDER)

# def allowed_file(filename):
#     """Check if the file has an allowed extension."""
#     return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# def map_dtype_to_mysql(dtype):
#     """Map pandas data types to MySQL data types."""
#     if pd.api.types.is_integer_dtype(dtype):
#         return 'INT'
#     elif pd.api.types.is_float_dtype(dtype):
#         return 'FLOAT'
#     elif pd.api.types.is_bool_dtype(dtype):
#         return 'BOOLEAN'
#     elif pd.api.types.is_string_dtype(dtype):
#         return 'TEXT'
#     elif pd.api.types.is_datetime64_any_dtype(dtype):
#         return 'DATETIME'
#     else:
#         return 'TEXT'

# def is_date_column(column):
#     """Check if a column is a date column."""
#     try:
#         pd.to_datetime(column, errors='raise')
#         return True
#     except:
#         return False

# def convert_to_datetime(column):
#     """Convert column to datetime."""
#     return pd.to_datetime(column, errors='coerce')

# @csrf_exempt
# def upload_file(request):
#     """Handle file upload and save to MySQL database."""
#     if request.method == 'POST':
#         # Get database connection details from request
#         host = request.POST.get('host')
#         user = request.POST.get('user')
#         password = request.POST.get('password')
#         database = request.POST.get('database')

#         # Check if all database connection details are provided
#         if not all([host, user, password, database]):
#             return JsonResponse({'error': 'Missing database connection details'}, status=400)

#         # Ensure file and table name are provided
#         if 'file' not in request.FILES or 'table_name' not in request.POST:
#             return JsonResponse({'error': 'No file or table name provided'}, status=400)

#         table_name = request.POST['table_name'].strip()
#         if not table_name:
#             return JsonResponse({'error': 'Table name cannot be empty'}, status=400)

#         file = request.FILES['file']
#         if file.name == '':
#             return JsonResponse({'error': 'No selected file'}, status=400)

#         # Check if the file type is allowed
#         if file and allowed_file(file.name):
#             filename = secure_filename(file.name)
#             filepath = os.path.join(UPLOAD_FOLDER, filename)

#             try:
#                 # Save the file to the server
#                 path = default_storage.save(filepath, ContentFile(file.read()))
#                 print("Step: File saved successfully")
#             except Exception as e:
#                 return JsonResponse({'error': 'Error saving file', 'message': str(e)}, status=500)

#             try:
#                 # Load CSV data into a pandas DataFrame
#                 data = pd.read_csv(filepath, low_memory=False)
#                 print("Step: CSV data loaded")
#                 if data.empty:
#                     return JsonResponse({'error': 'Uploaded file is empty'}, status=400)
#             except pd.errors.ParserError as e:
#                 return JsonResponse({'error': 'Error parsing CSV file', 'message': str(e)}, status=500)
#             except Exception as e:
#                 return JsonResponse({'error': 'Error reading file', 'message': str(e)}, status=500)

#             try:
#                 # Data processing: Convert date columns and optimize data types
#                 for col in data.columns:
#                     if is_date_column(data[col]):
#                         data[col] = convert_to_datetime(data[col])

#                 for col in data.columns:
#                     if not is_date_column(data[col]):
#                         if pd.api.types.is_integer_dtype(data[col]):
#                             data[col] = pd.to_numeric(data[col], downcast='integer')
#                         elif pd.api.types.is_float_dtype(data[col]):
#                             data[col] = pd.to_numeric(data[col], downcast='float')

#                 # Create SQLAlchemy engine for MySQL connection
#                 engine = create_engine(f'mysql+pymysql://{user}:{password}@{host}/{database}')

#                 # Save data to MySQL table (replace if table already exists)
#                 data.to_sql(table_name, engine, if_exists='replace', index=False)
#                 print("Step: Data saved to MySQL")

#                 # Return success response
#                 return JsonResponse({'message': f'File uploaded and data saved to table "{table_name}" successfully!'})

#             except SQLAlchemyError as e:
#                 return JsonResponse({'error': 'Database error', 'message': str(e)}, status=500)
#             except Exception as e:
#                 return JsonResponse({'error': 'Error saving data to database', 'message': str(e)}, status=500)

#         return JsonResponse({'error': 'Invalid file type'}, status=400)
    
#     return JsonResponse({'error': 'Invalid request method'}, status=405)