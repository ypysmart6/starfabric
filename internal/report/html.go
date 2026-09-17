package report

import (
	"html/template"
	"io"
	"time"

	"github.com/starfabric/starfabric/internal/scenario"
)

var page = template.Must(template.New("report").Funcs(template.FuncMap{
	"duration": func(start, end time.Time) string { return end.Sub(start).String() },
}).Parse(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StarFabric report — {{.ScenarioID}}</title>
<style>
body{font:15px system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;color:#16202a;background:#f7f9fb}
h1,h2{color:#102a43}.card{background:white;border:1px solid #d9e2ec;border-radius:10px;padding:18px;margin:16px 0}
.ok{color:#087f5b}.bad{color:#c92a2a}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:9px;border-bottom:1px solid #e9ecef}code{font-size:13px}
</style></head><body><h1>StarFabric experiment report</h1>
<div class="card"><strong>Scenario:</strong> {{.ScenarioID}}<br><strong>Result:</strong> {{if .Success}}<span class="ok">PASS</span>{{else}}<span class="bad">FAIL</span>{{end}}<br>
<strong>Seed:</strong> {{.Seed}} · <strong>Duration:</strong> {{duration .StartedAt .FinishedAt}} · <strong>Rollbacks:</strong> {{.RollbackCount}} · <strong>Failures:</strong> {{.FailureCount}}</div>
<h2>Timeline</h2><div class="card"><table><thead><tr><th>At</th><th>Action</th><th>Topology</th><th>Plan</th><th>Duration</th><th>Result</th></tr></thead><tbody>
{{range .Steps}}<tr><td>{{.AtMS}} ms</td><td><code>{{.Action.Type}}</code></td><td>v{{.TopologyVersion}}</td><td><code>{{.PlanID}}</code></td><td>{{.DurationUS}} μs</td><td>{{if .ExpectedFailure}}<span class="ok">PASS (expected rollback)</span>{{else if .Error}}<span class="bad">{{.Error}}</span>{{else}}<span class="ok">PASS</span>{{end}}</td></tr>{{end}}
</tbody></table></div><h2>Final state</h2><div class="card">Topology v{{.FinalTopology.Version}} · {{len .FinalTopology.Nodes}} nodes · {{len .FinalTopology.Links}} directed links<br>
{{if .FinalPlan}}Committed plan: <code>{{.FinalPlan.ID}}</code>, {{len .FinalPlan.Routes}} routes{{else}}No plan committed{{end}}</div></body></html>`))

func HTML(w io.Writer, value scenario.Report) error { return page.Execute(w, value) }
