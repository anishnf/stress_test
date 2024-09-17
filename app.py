#!/usr/bin/env python3
import os
import uuid
import random
import string
import boto3
from datetime import datetime, timedelta
import pytz
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
import time
from flask import Flask, render_template, jsonify, request
import threading

from flask import Flask, render_template, jsonify, request, send_from_directory

app = Flask(__name__, static_url_path='/static')

# Constants
FROM_ADDRESS = 'Max Rogers <max@starwoodhealth.com>'
TO_ADDRESS = 'Anish Kelkar <anish@nightfall.ai>'
COMPANY_UUID = "8ef6665d-256a-4b43-8c32-910ef419d32b"
S3_BUCKET = "nf-inline-email-storage-dev"

# Global variables for progress tracking
upload_progress = 0
upload_complete = False
start_time = None
total_emails = 0
upload_rate = 0

@app.route('/generate_and_upload', methods=['POST'])
def generate_and_upload():
    global upload_progress, upload_complete, start_time, total_emails, upload_rate
    upload_progress = 0
    upload_complete = False
    start_time = None
    
    num_emails = int(request.form['num_emails'])
    rate = float(request.form['rate'])
    
    # Generate files
    generator = EmailGenerator(num_emails)
    output_dir, postgres_query, generated_files = generator.generate_files()
    
    # Start upload
    total_emails = num_emails
    upload_rate = rate
    bucket = connect_to_s3()
    if bucket:
        threading.Thread(target=upload_to_s3, args=(bucket, output_dir, num_emails, rate)).start()
        return jsonify({
            "status": "started", 
            "duration": num_emails / rate,
            "generated_files": generated_files
        })
    else:
        return jsonify({"status": "error", "message": "Failed to connect to S3. Upload aborted."})

class EmailGenerator:
    def __init__(self, num_emails):
        self.num_emails = num_emails
        self.current_time = datetime.now()
        self.batch_number = self.current_time.strftime('%d-%m-%y-%H-%M')
        self.output_dir = os.path.join('generated_emails', self.batch_number)
        os.makedirs(self.output_dir, exist_ok=True)
        self.prefix = self.current_time.strftime('%m%d%H%M') + '_stress_test'
        self.company_uuid = COMPANY_UUID
        self.created_at = self.current_time

    def generate_message_id(self):
        uppercase_letters = string.ascii_uppercase
        lowercase_letters = string.ascii_lowercase
        digits = string.digits
        first_part = ''.join(random.choices(uppercase_letters + digits, k=10))
        s_count = random.randint(5, 10)
        middle_part = 's' * s_count
        remaining_length = 44 - len(first_part) - len(middle_part) - 1
        last_part = ''.join(random.choices(lowercase_letters + digits, k=remaining_length))
        return f'<{first_part}{middle_part}-{last_part}@mail.gmail.com>'

    def get_template(self):
        return open('template.txt', 'r').read()

    def generate_files(self):
        template = self.get_template()
        postgres_query = ''
        generated_files = []

        for i in range(self.num_emails):
            message_id = self.generate_message_id()
            pacific_tz = pytz.timezone('US/Pacific')
            current_time = datetime.now(pacific_tz)
            formatted_date = current_time.strftime('%a, %d %b %Y %H:%M:%S %z')

            # Update subject line to include batch number and email number
            subject_line = f'Stress test Batch# {self.batch_number} Email# {i + 1}'

            content = template.format(
                message_id=message_id,
                i=i,
                formatted_date=formatted_date,
                from_address=FROM_ADDRESS,
                to_address=TO_ADDRESS,
                subject_line=subject_line
            )

            filename = f'{self.prefix}_email_{i + 1}'
            file_path = os.path.join(self.output_dir, filename)

            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(content)

            generated_files.append(filename)

            email_uuid = str(uuid.uuid4())
            postgres_query += self.get_postgres_query_row(email_uuid, filename)

        print(f'Generated {self.num_emails} email files in {self.output_dir}')
        return self.output_dir, postgres_query.rstrip(',\n'), generated_files
        template = self.get_template()
        postgres_query = self.get_postgres_query_template()
        generated_files = []

        for i in range(self.num_emails):
            message_id = self.generate_message_id()
            
            pacific_tz = pytz.timezone('US/Pacific')
            current_time = datetime.now(pacific_tz)
            formatted_date = current_time.strftime("%a, %d %b %Y %H:%M:%S %z")
            
            content = template.format(
                message_id=message_id,
                i=i,
                formatted_date=formatted_date,
                from_address=FROM_ADDRESS,
                to_address=TO_ADDRESS
            )
            
            filename = f"{self.prefix}_email_{i+1}"
            file_path = os.path.join(self.output_dir, filename)
            
            with open(file_path, "w", encoding="utf-8") as file:
                file.write(content)
            
            generated_files.append(filename)
            
            email_uuid = str(uuid.uuid4())
            postgres_query += self.get_postgres_query_row(email_uuid, filename)

        print(f"Generated {self.num_emails} email files in {self.output_dir}")
        
        return self.output_dir, postgres_query.rstrip(',\n'), generated_files

    def get_postgres_query_template(self):
        return """
                WITH insert_data AS (
                    SELECT * FROM (
                        VALUES
                """

    def get_postgres_query_row(self, email_uuid, filename):
        return f"""            ('{email_uuid}'::uuid, '{self.company_uuid}'::uuid, '{filename}', 0, '{self.created_at.isoformat()}'::timestamp with time zone, '{{}}'::jsonb, '{{}}'::jsonb, '{self.created_at.isoformat()}'::timestamp with time zone, FALSE, 0, NULL::uuid, '[]'::json, 0),\n"""

    def complete_postgres_query(self, query):
        return query + """
                        ) AS t(email_uuid, company_uuid, location, attachments, created_at, added_headers, encryption_metadata, last_ingested_at, scanned, scanned_attachments, violation_uuid, violation_uuids, action_count)
                    )
                    INSERT INTO public.email (
                        email_uuid,
                        company_uuid,
                        location,
                        attachments,
                        created_at,
                        added_headers,
                        encryption_metadata,
                        last_ingested_at,
                        scanned,
                        scanned_attachments,
                        violation_uuid,
                        violation_uuids,
                        action_count
                    )
                    SELECT * FROM insert_data;
                    """

def connect_to_s3():
    try:
        session = boto3.Session(region_name='us-west-2')
        s3 = session.resource('s3')
        bucket = s3.Bucket(S3_BUCKET)
        print(f"Session created and bucket {S3_BUCKET} connected")
        return bucket
    except (NoCredentialsError, PartialCredentialsError) as e:
        print(f"Error connecting to S3: {str(e)}")
        return None

def get_latest_folder(base_dir="generated_emails"):
    folders = [f for f in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, f))]
    if not folders:
        return None
    latest_folder = max(folders, key=lambda f: os.path.getmtime(os.path.join(base_dir, f)))
    return os.path.join(base_dir, latest_folder)

def upload_to_s3(bucket, folder_path, num_emails, rate):
    global upload_progress, upload_complete, start_time, total_emails, upload_rate, uploaded_files
    files = os.listdir(folder_path)
    total_files = min(len(files), num_emails)
    uploaded_count = 0
    start_time = datetime.now()
    uploaded_files = []

    print(f"Starting upload at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Uploading {total_files} emails at a rate of {rate:.2f} emails per second")

    for filename in files[:total_files]:
        file_path = os.path.join(folder_path, filename)
        s3_key = filename
        bucket.upload_file(file_path, s3_key)
        uploaded_count += 1
        upload_progress = (uploaded_count / total_files) * 100
        uploaded_files.append(filename)

        log_message = f"Uploaded file: {filename} - Uploaded {uploaded_count}/{total_files} emails. Progress: {upload_progress:.2f}%"
        print(log_message)

        if uploaded_count < total_files:
            time.sleep(1 / rate)  # Sleep to maintain the desired rate

    upload_complete = True
    print(f"\nUpload completed. Total emails uploaded: {uploaded_count}")

def start_upload(num_emails, rate):
    global total_emails, upload_rate
    total_emails = num_emails
    upload_rate = rate
    bucket = connect_to_s3()
    if bucket:
        latest_folder = get_latest_folder()
        if latest_folder:
            threading.Thread(target=upload_to_s3, args=(bucket, latest_folder, num_emails, rate)).start()
        else:
            print("No folders found to upload.")
    else:
        print("Failed to connect to S3. Upload aborted.")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_upload', methods=['POST'])
def start_upload_route():
    global upload_progress, upload_complete, start_time
    upload_progress = 0
    upload_complete = False
    start_time = None
    
    num_emails = int(request.form['num_emails'])
    rate = float(request.form['rate'])
    
    start_upload(num_emails, rate)
    return jsonify({"status": "started", "duration": num_emails / rate})

@app.route('/progress')
def progress():
    global upload_progress, upload_complete, start_time, total_emails, upload_rate, uploaded_files
    if start_time:
        elapsed_time = (datetime.now() - start_time).total_seconds()
    else:
        elapsed_time = 0
    return jsonify({
        "progress": upload_progress,
        "complete": upload_complete,
        "elapsed_time": elapsed_time,
        "total_emails": total_emails,
        "upload_rate": upload_rate,
        "uploaded_files": uploaded_files
    })

if __name__ == "__main__":
    app.run(debug=True)