{{/* Image reference: repository[:tag][@digest]. */}}
{{- define "webodm.image" -}}
{{- .repository -}}
{{- with .tag }}:{{ . }}{{ end -}}
{{- with .digest }}@{{ . }}{{ end -}}
{{- end -}}

{{/* Labels. Call with (dict "ctx" $ "component" "frappe-web"). */}}
{{- define "webodm.selectorLabels" -}}
app.kubernetes.io/name: webodm
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "webodm.labels" -}}
{{ include "webodm.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .ctx.Chart.Name .ctx.Chart.Version }}
{{- end -}}

{{/* Pod label + affinity that keep the pods sharing ReadWriteOnce claims on one node. */}}
{{- define "webodm.sharedStorageLabel" -}}
{{- if .Values.persistence.colocate }}
webodm/shared-storage: "true"
{{- end }}
{{- end -}}

{{- define "webodm.sharedStorageAffinity" -}}
{{- if .Values.persistence.colocate }}
affinity:
  podAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - topologyKey: kubernetes.io/hostname
        labelSelector:
          matchLabels:
            app.kubernetes.io/instance: {{ .Release.Name }}
            webodm/shared-storage: "true"
{{- end }}
{{- end -}}

{{/* Pod-spec fields common to every workload. */}}
{{- define "webodm.podCommon" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "webodm.storageClass" -}}
{{- with .Values.storageClass }}
storageClassName: {{ . | quote }}
{{- end }}
{{- end -}}

{{- define "webodm.siteName" -}}
{{- .Values.site.name | default .Values.site.domain -}}
{{- end -}}

{{/* One env entry read from the credentials Secret.
     Call with (dict "ctx" $ "name" "DB_PASSWORD" "key" "db_password" "optional" false). */}}
{{- define "webodm.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .ctx.Values.secrets.name }}
      key: {{ .key }}
      {{- if .optional }}
      optional: true
      {{- end }}
{{- end -}}

{{/* Object storage settings, resolved against the in-cluster MinIO when enabled. */}}
{{- define "webodm.s3.bucket" -}}
{{- if .Values.minio.enabled -}}
{{- .Values.objectStorage.bucket | default "webodm" -}}
{{- else -}}
{{- .Values.objectStorage.bucket -}}
{{- end -}}
{{- end -}}

{{- define "webodm.s3.endpoint" -}}
{{- if .Values.minio.enabled -}}
{{- .Values.objectStorage.endpointUrl | default "http://minio:9000" -}}
{{- else -}}
{{- .Values.objectStorage.endpointUrl -}}
{{- end -}}
{{- end -}}

{{- define "webodm.s3.region" -}}
{{- if .Values.minio.enabled -}}
{{- .Values.objectStorage.region | default "us-east-1" -}}
{{- else -}}
{{- .Values.objectStorage.region -}}
{{- end -}}
{{- end -}}

{{- define "webodm.s3.pathStyle" -}}
{{- if or .Values.minio.enabled .Values.objectStorage.forcePathStyle -}}true{{- end -}}
{{- end -}}

{{- define "webodm.provisionerUrl" -}}
{{- if and .Values.provisioner.enabled (ne .Values.provisioner.provider "none") -}}
http://provisioner:5002
{{- end -}}
{{- end -}}

{{/* Non-secret environment shared by every Frappe role (and hashed into the
     bootstrap Job name). Same contract as x-frappe-env-base in docker-compose.yml. */}}
{{- define "webodm.frappe.configEnv" -}}
- name: SITE_NAME
  value: {{ include "webodm.siteName" . | quote }}
- name: DB_HOST
  value: postgres
- name: DB_PORT
  value: "5432"
- name: DB_NAME
  value: {{ .Values.database.name | quote }}
- name: DB_USER
  value: {{ .Values.database.user | quote }}
- name: FRAPPE_ROOT_USER
  value: frappe_admin
- name: REDIS_CACHE_HOST
  value: redis-cache
- name: REDIS_CACHE_PORT
  value: "13000"
- name: REDIS_QUEUE_HOST
  value: redis-queue
- name: REDIS_QUEUE_PORT
  value: "11000"
- name: GEOSPATIAL_URL
  value: http://geospatial:5000
- name: PLUGIN_RUNNER_URL
  value: http://plugin-runner:5001
- name: PLUGIN_SANDBOX_DIR
  value: /sandbox
- name: FRAPPE_SOCKETIO_PORT
  value: "9000"
- name: PROVISIONER_URL
  value: {{ include "webodm.provisionerUrl" . | quote }}
- name: WEBODM_S3_BUCKET
  value: {{ include "webodm.s3.bucket" . | quote }}
- name: WEBODM_S3_PREFIX
  value: {{ .Values.objectStorage.prefix | quote }}
- name: WEBODM_S3_REGION
  value: {{ include "webodm.s3.region" . | quote }}
- name: WEBODM_S3_ENDPOINT_URL
  value: {{ include "webodm.s3.endpoint" . | quote }}
- name: WEBODM_S3_FORCE_PATH_STYLE
  value: {{ ternary "1" "0" (eq (include "webodm.s3.pathStyle" .) "true") | quote }}
- name: WEBODM_CACHE_MAX_BYTES
  value: {{ .Values.objectStorage.cacheMaxBytes | quote }}
- name: WEBODM_CACHE_IDLE_SECONDS
  value: {{ .Values.objectStorage.cacheIdleSeconds | quote }}
{{- range $name, $value := .Values.frappe.env }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- end -}}

{{/* Credentials for the Frappe roles. entrypoint.sh takes them as plain
     variables (the *_FILE form is the compose equivalent). */}}
{{- define "webodm.frappe.secretEnv" -}}
{{ include "webodm.secretEnv" (dict "ctx" . "name" "DB_PASSWORD" "key" "db_password") }}
{{ include "webodm.secretEnv" (dict "ctx" . "name" "ADMIN_PASSWORD" "key" "admin_password") }}
{{ include "webodm.secretEnv" (dict "ctx" . "name" "FRAPPE_ROOT_PASSWORD" "key" "frappe_admin_password") }}
{{ include "webodm.secretEnv" (dict "ctx" . "name" "REDIS_CACHE_PASSWORD" "key" "redis_cache_password") }}
{{ include "webodm.secretEnv" (dict "ctx" . "name" "REDIS_QUEUE_PASSWORD" "key" "redis_queue_password") }}
{{ include "webodm.secretEnv" (dict "ctx" . "name" "PROVISIONER_API_TOKEN" "key" "provisioner_api_token") }}
{{ include "webodm.secretEnv" (dict "ctx" . "name" "WEBODM_NODE_TOKEN_SECRET" "key" "node_token_secret") }}
{{ include "webodm.secretEnv" (dict "ctx" . "name" "WEBODM_S3_ACCESS_KEY_ID" "key" "s3_app_access_key_id" "optional" true) }}
{{ include "webodm.secretEnv" (dict "ctx" . "name" "WEBODM_S3_SECRET_ACCESS_KEY" "key" "s3_app_secret_access_key" "optional" true) }}
{{- end -}}

{{- define "webodm.frappe.env" -}}
{{ include "webodm.frappe.configEnv" . }}
{{ include "webodm.frappe.secretEnv" . }}
{{- end -}}

{{/* A Job's pod template is immutable, so its name must hash the WHOLE template:
     a changed image, env, imagePullSecrets/nodeSelector/tolerations (podCommon),
     affinity, resources or volumes has to produce a new Job, otherwise a later
     `helm upgrade` fails with "spec.template: ... field is immutable". The name
     helpers hash the rendered template (plus bootstrap.runId, the force-a-rerun
     escape hatch) rather than a hand-picked list of inputs. */}}
{{- define "webodm.bootstrap.podTemplate" -}}
metadata:
  labels:
    {{- include "webodm.selectorLabels" (dict "ctx" . "component" "frappe-init") | nindent 4 }}
    webodm/frappe: "true"
    {{- include "webodm.sharedStorageLabel" . | nindent 4 }}
spec:
  {{- include "webodm.podCommon" . | nindent 2 }}
  {{- include "webodm.sharedStorageAffinity" . | nindent 2 }}
  automountServiceAccountToken: false
  restartPolicy: OnFailure
  initContainers:
    - name: wait-for-datastores
      image: {{ include "webodm.image" .Values.images.frappe }}
      imagePullPolicy: {{ .Values.images.frappe.pullPolicy }}
      command:
        - bash
        - -c
        - |
          until pg_isready -q -h postgres -p 5432; do echo "waiting for postgres"; sleep 3; done
          for target in redis-cache:13000 redis-queue:11000; do
            until (exec 3<>"/dev/tcp/${target%:*}/${target#*:}") 2>/dev/null; do echo "waiting for ${target}"; sleep 3; done
          done
  containers:
    - name: frappe-init
      image: {{ include "webodm.image" .Values.images.frappe }}
      imagePullPolicy: {{ .Values.images.frappe.pullPolicy }}
      env:
        - name: FRAPPE_ROLE
          value: init
        {{- include "webodm.frappe.env" . | nindent 8 }}
      {{- with .Values.bootstrap.resources }}
      resources:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      volumeMounts:
        - name: sites
          mountPath: /workspace/frappe-bench/sites
  volumes:
    - name: sites
      persistentVolumeClaim:
        claimName: frappe-sites
{{- end -}}

{{- define "webodm.bootstrap.jobName" -}}
{{- $inputs := printf "%s\n%s" (include "webodm.bootstrap.podTemplate" .) .Values.bootstrap.runId -}}
frappe-init-{{ $inputs | sha256sum | trunc 10 }}
{{- end -}}

{{- define "webodm.minio.podTemplate" -}}
metadata:
  labels:
    {{- include "webodm.selectorLabels" (dict "ctx" . "component" "minio-init") | nindent 4 }}
spec:
  {{- include "webodm.podCommon" . | nindent 2 }}
  automountServiceAccountToken: false
  restartPolicy: OnFailure
  containers:
    - name: minio-init
      image: {{ include "webodm.image" .Values.images.mc }}
      imagePullPolicy: {{ .Values.images.mc.pullPolicy }}
      command: [/bin/sh, /scripts/minio-init.sh]
      env:
        - name: MINIO_ENDPOINT
          value: http://minio:9000
        - name: BUCKET
          value: {{ include "webodm.s3.bucket" . | quote }}
        # mc keeps its alias config here; the default home is not writable.
        - name: MC_CONFIG_DIR
          value: /tmp/mc
        {{- include "webodm.secretEnv" (dict "ctx" . "name" "MINIO_ROOT_USER" "key" "minio_root_user") | nindent 8 }}
        {{- include "webodm.secretEnv" (dict "ctx" . "name" "MINIO_ROOT_PASSWORD" "key" "minio_root_password") | nindent 8 }}
        {{- include "webodm.secretEnv" (dict "ctx" . "name" "APP_ACCESS_KEY_ID" "key" "s3_app_access_key_id") | nindent 8 }}
        {{- include "webodm.secretEnv" (dict "ctx" . "name" "APP_SECRET_ACCESS_KEY" "key" "s3_app_secret_access_key") | nindent 8 }}
        {{- include "webodm.secretEnv" (dict "ctx" . "name" "GEO_ACCESS_KEY_ID" "key" "s3_geospatial_access_key_id" "optional" true) | nindent 8 }}
        {{- include "webodm.secretEnv" (dict "ctx" . "name" "GEO_SECRET_ACCESS_KEY" "key" "s3_geospatial_secret_access_key" "optional" true) | nindent 8 }}
      volumeMounts:
        - name: scripts
          mountPath: /scripts
          readOnly: true
        - name: tmp
          mountPath: /tmp
  volumes:
    - name: scripts
      configMap:
        name: webodm-scripts
    - name: tmp
      emptyDir: {}
{{- end -}}

{{- define "webodm.minio.jobName" -}}
{{- $inputs := printf "%s\n%s" (include "webodm.minio.podTemplate" .) (.Files.Get "files/minio-init.sh") -}}
minio-init-{{ $inputs | sha256sum | trunc 10 }}
{{- end -}}

{{/* Init container that blocks until the bootstrap Job (and, with MinIO, the
     bucket Job) has completed. Renders nothing when there is nothing to wait for. */}}
{{- define "webodm.waitForBootstrap" -}}
{{- if or .Values.bootstrap.enabled .Values.minio.enabled }}
- name: wait-for-bootstrap
  image: {{ include "webodm.image" .Values.images.kubectl }}
  imagePullPolicy: {{ .Values.images.kubectl.pullPolicy }}
  command:
    - kubectl
    - wait
    - --namespace={{ .Release.Namespace }}
    - --for=condition=complete
    - --timeout={{ .Values.bootstrap.waitTimeout }}
    {{- if .Values.bootstrap.enabled }}
    - job/{{ include "webodm.bootstrap.jobName" . }}
    {{- end }}
    {{- if .Values.minio.enabled }}
    - job/{{ include "webodm.minio.jobName" . }}
    {{- end }}
  securityContext:
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    runAsNonRoot: true
    runAsUser: 65532
    capabilities:
      drop: [ALL]
  resources:
    requests:
      cpu: 10m
      memory: 32Mi
    limits:
      memory: 128Mi
{{- end }}
{{- end -}}

{{/* Fail early on value combinations that cannot work. */}}
{{- define "webodm.validate" -}}
{{- if not .Values.secrets.name }}
{{- fail "secrets.name is required: the name of the Secret holding the credentials" }}
{{- end }}
{{- if .Values.secrets.create }}
{{- range $key := list "db_password" "admin_password" "frappe_admin_password" "redis_cache_password" "redis_queue_password" "provisioner_api_token" "node_token_secret" }}
{{- if not (get $.Values.secrets.values $key) }}
{{- fail (printf "secrets.create=true needs secrets.values.%s" $key) }}
{{- end }}
{{- end }}
{{- if .Values.minio.enabled }}
{{- range $key := list "minio_root_user" "minio_root_password" "s3_app_access_key_id" "s3_app_secret_access_key" }}
{{- if not (get $.Values.secrets.values $key) }}
{{- fail (printf "secrets.create=true with minio.enabled needs secrets.values.%s" $key) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- if not (has .Values.caddy.tls.mode (list "auto" "internal" "off")) }}
{{- fail "caddy.tls.mode must be auto, internal or off" }}
{{- end }}
{{- if not (has .Values.caddy.tls.dnsProvider (list "" "cloudflare" "route53")) }}
{{- fail "caddy.tls.dnsProvider must be empty, cloudflare or route53" }}
{{- end }}
{{- if and .Values.caddy.ingress.enabled (ne .Values.caddy.tls.mode "off") }}
{{- fail "caddy.ingress.enabled needs caddy.tls.mode=off (the Ingress terminates TLS and forwards plain HTTP)" }}
{{- end }}
{{- if and .Values.caddy.proxyProtocol.enabled (not .Values.caddy.proxyProtocol.allow) }}
{{- fail "caddy.proxyProtocol.enabled needs caddy.proxyProtocol.allow (the load balancer's source CIDRs)" }}
{{- end }}
{{- if not (has .Values.provisioner.provider (list "none" "aws" "fixed")) }}
{{- fail "provisioner.provider must be none, aws or fixed" }}
{{- end }}
{{- if and .Values.minio.enabled .Values.objectStorage.endpointUrl (ne .Values.objectStorage.endpointUrl "http://minio:9000") }}
{{- fail "minio.enabled and objectStorage.endpointUrl point at different stores; set one or the other" }}
{{- end }}
{{- end -}}

{{/* tls directive for a Caddy site block (see files/Caddyfile). Call with .Values.caddy.tls. */}}
{{- define "webodm.caddy.tls" }}
{{- if eq .mode "internal" }}
    tls internal
{{- else if and (eq .mode "auto") (eq .dnsProvider "cloudflare") }}
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
{{- else if and (eq .mode "auto") (eq .dnsProvider "route53") }}
    tls {
        dns route53
    }
{{- end }}
{{- end -}}
