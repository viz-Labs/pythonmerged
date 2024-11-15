from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from pymongo import MongoClient
from bson import ObjectId
from langchain.llms import Ollama
import json
import threading

# MongoDB connection details
mongo_uri = "mongodb+srv://prince961:XRcDfeLA6foxoWtz@cluster0.ox5zuer.mongodb.net/CompanyDB?retryWrites=true&w=majority"
client = MongoClient(mongo_uri)
db = client['CompanyDB']
datasources_collection = db['datasources']
uniquevalues_collection = db['uniquevalues']

# Create an instance of the Ollama model
ollama_instance = Ollama(base_url="http://122.176.146.28:11434", model="test09")

# Function to fetch column names and UUIDs
def fetch_column_info(column_document_id):
    try:
        document = datasources_collection.find_one({"_id": ObjectId(column_document_id)}, {"ColumnDetails": 1})
        if document and "ColumnDetails" in document:
            return [{"Column Name": col["columnName"], "UUID": col["uuid"]} for col in document["ColumnDetails"]]
        return []
    except Exception as e:
        return {'error': f'Error fetching column info: {str(e)}'}

# Function to fetch unique values for columns with string data types
def fetch_string_unique_values(unique_values_document_id):
    try:
        document = uniquevalues_collection.find_one({"_id": ObjectId(unique_values_document_id)}, {"UniqueValues": 1})
        if document and "UniqueValues" in document:
            return {col: values for col, values in document["UniqueValues"].items() if isinstance(values, list) and all(isinstance(v, str) for v in values)}
        return {}
    except Exception as e:
        return {'error': f'Error fetching unique values: {str(e)}'}

# Function to prepare the initial query with combined column and unique values information
def prepare_initial_query(column_info, string_unique_values):
    column_info_str = "\n".join(
        [f"Column Name: {col['Column Name']}, UUID: {col['UUID']}, Unique Values: {', '.join(string_unique_values.get(col['Column Name'], []))}"
         for col in column_info]
    )
    return f"Our task is to assist users in analyzing the following data:\n{column_info_str}\nPlease provide a response including both column names with their UUIDs and the associated unique values for each column where applicable."

@csrf_exempt
def api_ask(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            user_query = data.get('query')
            column_document_id = data.get('column_document_id')
            unique_values_document_id = data.get('unique_values_document_id')
            
            if not user_query or not column_document_id or not unique_values_document_id:
                return JsonResponse({'response': 'Missing query, column_document_id, or unique_values_document_id'}, status=400)
            
            # Fetch column and unique values data
            column_info = fetch_column_info(column_document_id)
            string_unique_values = fetch_string_unique_values(unique_values_document_id)
            
            if isinstance(column_info, dict) and column_info.get('error'):
                return JsonResponse({'response': column_info['error']}, status=500)
            if isinstance(string_unique_values, dict) and string_unique_values.get('error'):
                return JsonResponse({'response': string_unique_values['error']}, status=500)
            
            if column_info:
                initial_query = prepare_initial_query(column_info, string_unique_values)
                full_query = initial_query + f"\nQuestion: {user_query}\nPlease provide a detailed response, including the UUID and unique values for each detail provided in the table."
                
                # Call the Ollama model to get the response
                response = ollama_instance(full_query)
                
                if response:
                    return JsonResponse({'response': response})
                return JsonResponse({'response': 'Error from Ollama model'}, status=500)
            
            return JsonResponse({'response': 'Error fetching column details or unique values.'}, status=500)
        
        except Exception as e:
            return JsonResponse({'response': f'Error processing request: {str(e)}'}, status=500)

    return JsonResponse({'response': 'Invalid request method'}, status=405)