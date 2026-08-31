import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'nginx';
const BASE_PORT = 80;
const bank_csvFile = open('bank_statement_large.csv', 'b');
const ledger_csvFile = open('ledger_large.csv', 'b');


export const options = {
    stages: [
        { duration: '2m', target: 500 },
        { duration: '3m', target: 1000 },
        { duration: '5m', target: 1000 },
        { duration: '3m', target: 1500 },
        { duration: '5m', target: 1500 },
        { duration: '3m', target: 0 },
    ],
    thresholds: {
        http_req_failed: ['rate<0.10'],
        'http_req_duration{endpoint:upload}': ['p(95)<1000', 'p(99)<3000'],
        'http_req_duration{endpoint:Get by Job ID}': ['p(95)<500', 'p(99)<3000'],
        'http_req_duration{endpoint:Get Result}': ['p(95)<1000', 'p(99)<3000'],
        'http_req_duration{endpoint:Get All Jobs}': ['p(95)<500', 'p(99)<3000'],
    },
};

export default function () {
    const uploadResponse = http.post(
        `http://${BASE_URL}:${BASE_PORT}/jobs/upload/`,
        {
            bank_file: http.file(bank_csvFile, 'tests/bank_statement_large.csv'),
            ledger_file: http.file(ledger_csvFile, 'tests/ledger_large.csv')
        },
        {
            tags: { endpoint:'upload',},
        }
    );

    check(uploadResponse, {
        'upload status 202': (r) => r.status === 202,
    });

    if (uploadResponse.status !== 202) {
        console.error(uploadResponse.body);
        return;
    }

    const jobId = uploadResponse.json('job_id');

    sleep(Math.random() * 4 + 1);

    const statusResponse = http.get(
        `http://${BASE_URL}:${BASE_PORT}/jobs/${jobId}/status/`,
        {
            tags: { endpoint:'Get by Job ID',},
        }
    );

    check(statusResponse, {
        'status endpoint 200': (r) => r.status === 200,
    });

    sleep(Math.random() * 4 + 1);

    const jobsResponse = http.get(
        `http://${BASE_URL}:${BASE_PORT}/jobs/`,
            {
            tags: { endpoint:'Get All Jobs',},
        }
    );

    check(jobsResponse, {
        'jobs endpoint 200': (r) => r.status === 200,
    });

    sleep(Math.random() * 4 + 1);

    const resultResponse = http.get(
        `http://${BASE_URL}:${BASE_PORT}/jobs/${jobId}/result/`,
        {
            tags: { endpoint:'Get Result',},
        }
    );

    check(resultResponse, {
        'result endpoint 200': (r) => r.status === 200,
    });
}