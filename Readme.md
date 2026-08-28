# AI-Powered Transaction Processing Pipeline

Backend + DevOps Internship Assignment

## Overview

This project processes raw financial transaction CSV files asynchronously using Django REST Framework, Celery, Redis, PostgreSQL, and an LLM.

Uploaded transaction files are cleaned, analyzed for anomalies, categorized using an LLM, and summarized into a structured report that can be retrieved through polling APIs.

The entire system runs using Docker Compose with a single startup command.

---


## Quick Setup

### Clone Repository

```bash
git clone <repository-url>
cd AI_transaction_processing_pipeline
```

### Configure Environment Variables

Create a `.env.local and .env.docekr` file in the project root:

a .env.local.example is provided in the project

```env
DB_NAME=postgres
DB_HOST =db
DB_USER=user-name
DB_PASSWORD=user-password
DB_PORT=5432

JWT_SECRET_KEY=user-secret-key

GROQ_API_KEY=visit here to get a GROQ API KEY https://console.groq.com/keys

CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

BASE_URL=localhost for local/ web for docker
```

### Start the Application (Docker)

```bash
docker compose up --build
```

This command starts:

* Django API (served through Gunicorn)
* PostgreSQL
* Redis
* Celery Worker

The API will be available at:

```text
http://localhost:8001
```
`FOR TESTING USING k6`

``` bash
sudo docker compose --profile stresstest up --build
```
---

## Local Development (Without Docker)

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the Django development server:

```bash
python manage.py runserver 8001
```

If running locally without Docker, ensure the following services are installed and running on your machine:

* PostgreSQL
* Redis
* Celery Worker

Start the Celery worker:

```bash
celery -A AI_transaction_processing_pipeline worker --loglevel=info
```

The Django development server is intended for local development only. Docker uses Gunicorn as the production WSGI server.


## Tech Stack

| Component          | Technology                     |
| ------------------ | ------------------------------ |
| Backend API        | Django REST Framework          |
| Database           | PostgreSQL                     |
| Task Queue         | Celery                         |
| Message Broker     | Redis                          |
| LLM Provider       | Groq (Llama 3.3 70B Versatile) |
| Containerization   | Docker & Docker Compose        |
| Application Server | Gunicorn                       |
| Data Processing    | Pandas                         |
| Load Testing       | k6                             |
| Profiling          | django-silk                    |

---

## Architecture
<img width="1232" height="761" alt="image" src="https://github.com/user-attachments/assets/32820a06-0a46-4c57-8a60-cf730fbb32c3" />

---

## Features

### CSV Upload

* Accepts transaction CSV files
* Creates a Job record immediately
* Queues processing asynchronously using Celery

### Data Cleaning

* Normalizes mixed date formats
* Removes currency symbols from amounts
* Converts currency values to uppercase
* Converts transaction status values to uppercase
* Fills missing categories with "Uncategorized"

### Anomaly Detection

Flags transactions when:

* Amount exceeds 3× account median spending
* Merchant is a domestic-only brand (Swiggy, Ola, IRCTC) but currency is USD

### LLM Classification

Transactions with missing categories are classified into:

* Food
* Shopping
* Travel
* Transport
* Utilities
* Cash Withdrawal
* Entertainment
* Other

Classification requests are batched to minimize API calls.

### LLM Summary Generation

Generates:

* Total spend by currency
* Top merchants
* Anomaly count
* Spending narrative
* Risk level

### Retry Logic

LLM requests are retried up to 3 times using exponential backoff.

Failed LLM responses do not fail the entire processing pipeline.

### Pagination

The Job Listing endpoint supports pagination.

Example:

```bash
GET /jobs/?page=1&page_size=20
```

---

## API Endpoints

### Upload CSV

```http
POST /jobs/upload/
```

Request:

```bash
curl -X POST \
  -F "file=@transactions.csv" \
  http://localhost:8001/jobs/upload/
```

Response:

```json
{
  "job_id": 1,
  "status": "pending"
}
```

---

### Get Job Status

```http
GET /jobs/{job_id}/status/
```

Example:

```bash
curl http://localhost:8001/jobs/1/status/
```

Response:

```json
{
  "id": 1,
  "status": "completed"
}
```

---

### Get Job Results

```http
GET /jobs/{job_id}/result/
```

Example:

```bash
curl http://localhost:8001/jobs/1/result/
```

Returns:

* Processed transactions
* Anomaly list
* Category breakdown
* LLM generated summary

---

### List Jobs

```http
GET /jobs/
```

Example:

```bash
curl http://localhost:8001/jobs/
```

Optional status filtering:

```http
GET /jobs/?status=completed
```

```bash
curl "http://localhost:8001/jobs/?status=failed"
```


Pagination:

```http
GET /jobs/?page=1&page_size=20
```

Response:

```json
{
  "count": 25,
  "page": 1,
  "page_size": 20,
  "results": [...]
}
```

---

## Data Model

### Job

```text
id
file
filename
status
created_at
completed_at
error
row_count_raw
row_count_clean
results (JSONField)
```

### Design Choice

The assignment suggested separate Job, Transaction, and JobSummary tables.

For simplicity and faster development, processed transaction data, anomalies, and summaries are stored inside a JSONField on the Job model.

Benefits:

* Simpler schema
* Fewer database writes
* Faster implementation

Trade-offs:

* Reduced query flexibility
* Larger database rows
* Less suitable for large-scale analytics workloads

---

## Load Testing

Load testing was performed using k6.

### Test Configuration

* Ramp-up to 600 Virtual Users
* Duration: 2.5 minutes
* Upload, Status, Result, and Job List endpoints tested

### Results

## Stress Test Results (600 VUs)

### Environment

* Django + DRF
* PostgreSQL 17
* Redis 7
* Celery
* Docker Compose (WSL2)
* k6 load testing

### Load Profile

```javascript
stages: [
  { duration: '30s', target: 50 },
  { duration: '30s', target: 100 },
  { duration: '30s', target: 200 },
  { duration: '30s', target: 400 },
  { duration: '30s', target: 600 },
]
```

### Resource Usage During Test

#### ~130 VUs

| Service    | CPU  | Memory |
| ---------- | ---- | ------ |
| Web        | 110% | 484 MB |
| Celery     | 110% | 328 MB |
| PostgreSQL | 98%  | 32 MB  |
| Redis      | 2.9% | 5.7 MB |

#### ~230 VUs

| Service    | CPU  | Memory |
| ---------- | ---- | ------ |
| Web        | 138% | 510 MB |
| Celery     | 10%  | 356 MB |
| PostgreSQL | 31%  | 198 MB |
| Redis      | 0.8% | 6.1 MB |

#### ~430 VUs

| Service    | CPU  | Memory |
| ---------- | ---- | ------ |
| Web        | 131% | 585 MB |
| Celery     | 11%  | 376 MB |
| PostgreSQL | 29%  | 198 MB |
| Redis      | 1.1% | 6.6 MB |

### Failure Point

At approximately 550-600 VUs:

* Upload requests started timing out
* Status requests started timing out
* Result requests started timing out

Observed k6 errors:

```text
Post /jobs/upload/: request timeout
Get /jobs/{id}/status/: request timeout
Get /jobs/{id}/result/: request timeout
```

### Key Findings

1. No PostgreSQL connection exhaustion occurred.
2. No Redis bottleneck observed.
3. Memory usage remained stable.
4. Celery workers remained healthy.
5. Application remained functional up to several hundred concurrent users.
6. First failure mode was request timeout, not process crash.
7. Previous "too many clients already" issue was resolved.

### Conclusion

The system successfully handled hundreds of concurrent virtual users without database exhaustion or memory collapse.

The primary bottleneck appears to be request throughput at the Django application layer, where request latency eventually exceeded k6 timeout thresholds under sustained load approaching 600 VUs.


#### 600 VUs

```text
Requests: 10,200
Throughput: ~60 requests/sec
Failure Rate: 0%
Average Response Time: 1.42s
p95 Response Time: 3.78s
```

#### Stress Testing

The application remained operational under heavy load but exhibited increased latency at higher virtual user counts.

Observed bottlenecks:

* Gunicorn worker concurrency
* PostgreSQL connection handling
* JSON serialization overhead
* Single-node deployment architecture

---

## Scalability Considerations

### Current Limitations

If traffic increased significantly, likely bottlenecks would include:

* Gunicorn worker count
* PostgreSQL connection limits
* Celery worker throughput
* JSONField growth
* Single API instance deployment

### Production Improvements

Potential improvements:

* Horizontal API scaling
* Multiple Celery workers
* PgBouncer connection pooling
* Redis caching layer
* Dedicated Transaction table
* Nginx reverse proxy
* Object storage (S3) for uploaded files
* Monitoring and observability tooling

Trade-off:

Greater scalability at the cost of increased operational complexity.

---

## Assumptions

* CSV files are reasonably sized and fit into memory.
* LLM responses return valid JSON.
* Groq API is available during processing.
* The application is intended for assignment evaluation and local development.

---

## Future Improvements

* Store transactions in a dedicated table
* Add authentication and authorization
* Add OpenAPI/Swagger documentation
* Add rate limiting
* Add distributed tracing and metrics
* Implement caching for job listings
* Add automated CI/CD pipelines

---

## Author

Abhinandan Thakur
