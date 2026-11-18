"""Secret-free contract for the seven-service Railway template.

Run this file to print the intended settings. Values containing secret() are
Railway template functions, never locally generated or staging credentials.
"""
import json

# Public release source; keep the /stack root directories below.
REPO = 'https://github.com/DanielleWashington/railway-observability-starter/tree/main'
SECRET = '${{secret(48,"abcdef0123456789")}}'
DISPLAY_NAMES = {
    'collector': 'Collector', 'prometheus': 'Prometheus', 'tempo': 'Tempo',
    'loki': 'Loki', 'grafana': 'Grafana', 'downstream': 'Downstream', 'api': 'API',
}


def ref(service, key='RAILWAY_PRIVATE_DOMAIN'):
    return '${{' + service + '.' + key + '}}'


def endpoint(service, port, path=''):
    return 'http://' + ref(service) + ':' + str(port) + path


LAYOUT = {
    'collector': ('services/collector', 13133, '/ready', '/var/lib/otelcol', .6),
    'prometheus': ('services/prometheus', 9090, '/api/v1/status/runtimeinfo', '/prometheus', 1),
    'tempo': ('services/tempo', 3200, '/ready', '/var/tempo', 1),
    'loki': ('services/loki', 3100, '/ready', '/var/loki', 1),
    'grafana': ('services/grafana', 3000, '/api/health', '/var/lib/grafana', .5),
    'downstream': ('examples/demo', 8080, '/health', None, .25),
    'api': ('examples/demo', 8080, '/health', None, .25),
}
DEMO = {
    'DEPLOYMENT_ENVIRONMENT': 'railway-template',
    'OTEL_EXPORTER_OTLP_ENDPOINT': endpoint('collector', 4318),
    'OTEL_EXPORTER_OTLP_PROTOCOL': 'http/protobuf',
    'OTLP_USERNAME': 'demo',
    'OTLP_PASSWORD': ref('collector', 'OTLP_PASSWORD'),
}
VARIABLES = {
    'collector': {
        'OTLP_PASSWORD': SECRET,
        'OTLP_HTPASSWD': 'demo:' + ref('collector', 'OTLP_PASSWORD'),
        'PROMETHEUS_OTLP_BASE_URL': endpoint('prometheus', 9090, '/api/v1/otlp'),
        'TEMPO_OTLP_BASE_URL': endpoint('tempo', 4318),
        'LOKI_OTLP_BASE_URL': endpoint('loki', 3100, '/otlp'),
    },
    'prometheus': {'COLLECTOR_METRICS_HOST': ref('collector')},
    'tempo': {},
    'loki': {},
    'grafana': {
        'GF_SECURITY_ADMIN_USER': 'admin',
        'GF_SECURITY_ADMIN_PASSWORD': SECRET,
        'GF_SECURITY_SECRET_KEY': SECRET,
        'GF_AUTH_ANONYMOUS_ENABLED': 'false',
        'GF_USERS_ALLOW_SIGN_UP': 'false',
        'GF_SECURITY_COOKIE_SECURE': 'true',
        'GF_SERVER_ROOT_URL': 'https://${{RAILWAY_PUBLIC_DOMAIN}}',
        'PROMETHEUS_QUERY_URL': endpoint('prometheus', 9090),
        'TEMPO_QUERY_URL': endpoint('tempo', 3200),
        'LOKI_QUERY_URL': endpoint('loki', 3100),
        'ALERT_WEBHOOK_URL': '',
        'ALERT_WEBHOOK_TOKEN': SECRET,
    },
    'downstream': {**DEMO, 'OTEL_SERVICE_NAME': 'demo-downstream',
                   'DEMO_TEST_TOKEN': ref('api', 'DEMO_TEST_TOKEN')},
    'api': {**DEMO, 'OTEL_SERVICE_NAME': 'demo-api', 'DEMO_TEST_TOKEN': SECRET,
            'DOWNSTREAM_URL': endpoint('downstream', 8080)},
}


def contract():
    services = {}
    for name, (source, port, health, mount, memory) in LAYOUT.items():
        variables = {'PORT': str(port), 'RAILWAY_DOCKERFILE_PATH': 'Dockerfile',
                     **VARIABLES[name]}
        if mount:
            variables['RAILWAY_RUN_UID'] = '0'
        # Keep local role keys stable; publish names and references with product casing.
        for key, value in variables.items():
            for role, display_name in DISPLAY_NAMES.items():
                value = value.replace('${{' + role + '.', '${{' + display_name + '.')
            variables[key] = value
        services[DISPLAY_NAMES[name]] = {
            'source': REPO, 'rootDirectory': '/stack/' + source,
            'dockerfilePath': 'Dockerfile', 'healthcheckPath': health,
            'healthcheckTimeout': None, 'numReplicas': 1,
            'sleepApplication': False, 'restartPolicyType': 'ON_FAILURE',
            'restartPolicyMaxRetries': 5,
            # The composer has no resource-cap fields. Consumer services inherit
            # workspace limits; these are suggested caps, not template settings.
            'suggestedLimits': {'memoryGB': memory, 'vCPUs': 1},
            'volumeMountPath': mount, 'volumeSizeMB': 5000 if mount else None,
            'publicPort': 3000 if name == 'grafana' else None,
            'variables': variables,
        }
    return {'name': 'OpenTelemetry Observability Starter',
            'description': 'Metrics, traces, logs, and alerts with OpenTelemetry and Grafana',
            'requiredInputs': ['Grafana.ALERT_WEBHOOK_URL'], 'services': services}


if __name__ == '__main__':
    print(json.dumps(contract(), indent=2))
