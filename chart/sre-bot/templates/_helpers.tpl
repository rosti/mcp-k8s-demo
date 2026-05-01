{{- define "sre-bot.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "sre-bot.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" -}}
{{- end -}}

{{- define "sre-bot.namespace" -}}
{{- default .Release.Namespace .Values.namespaceOverride -}}
{{- end -}}

{{- define "sre-bot.labels" -}}
helm.sh/chart: {{ include "sre-bot.chart" . }}
app.kubernetes.io/name: {{ include "sre-bot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
