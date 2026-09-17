{{- define "starfabric.name" -}}starfabric{{- end }}
{{- define "starfabric.fullname" -}}{{ .Release.Name }}-starfabric{{- end }}
{{- define "starfabric.labels" -}}
app.kubernetes.io/name: {{ include "starfabric.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

