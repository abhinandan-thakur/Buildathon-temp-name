import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'web';
const bank_csvFile = open('bank_statement.csv', 'b');
const ledger_csvFile = open('ledger.csv', 'b');


export const options = {
    stages: [
        { duration: '30s', target: 50 },
        { duration: '30s', target: 100 },
        { duration: '30s', target: 200 },
        { duration: '30s', target: 400 },
        { duration: '30s', target: 600 },
        { duration: '60s', target: 800 },
        { duration: '60s', target: 1000 },

    ],
    thresholds: {
        http_req_failed: ['rate<0.10'],
        http_req_duration: ['p(95)<1000', 'p(99)<3000',],
    },
};

export default function () {
    const uploadResponse = http.post(
        `http://${BASE_URL}:8001/jobs/upload/`,
        {
            bank_file: http.file(bank_csvFile, 'tests/bank_statement.csv'),
            ledger_file: http.file(ledger_csvFile, 'tests/ledger.csv')
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
        `http://${BASE_URL}:8001/jobs/${jobId}/status/`
    );

    check(statusResponse, {
        'status endpoint 200': (r) => r.status === 200,
    });

    sleep(Math.random() * 4 + 1);

    const jobsResponse = http.get(
        `http://${BASE_URL}:8001/jobs/`
    );

    check(jobsResponse, {
        'jobs endpoint 200': (r) => r.status === 200,
    });

    sleep(Math.random() * 4 + 1);

    const resultResponse = http.get(
        `http://${BASE_URL}:8001/jobs/${jobId}/result/`
    );

    check(resultResponse, {
        'result endpoint 200': (r) => r.status === 200,
    });
}