# Graph Report - TimeSheetCompiler  (2026-10-01)

## Corpus Check
- 51 files · ~22,317 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 404 nodes · 580 edges · 43 communities (26 shown, 17 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 21 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f562e50f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- requirements.txt (Dependency Manifest)
- get_token_valido
- Docente
- classificationpipeline/tests.py
- HookSettingsTests
- ShibbolethMiddlewareTests
- ._event
- Decisioni tecniche e appunti implementativi
- ClassificationpipelineConfig
- HooksConfig
- main
- authentication/migrations/0001_initial.py
- EventCompareTests
- EventCategoryConstraintTests
- ExpiryCutoffTests
- classificationpipeline/migrations/0001_initial.py
- 0002_alter_calendar_id_alter_event_id.py
- hooks/migrations/0001_initial.py
- asgi.py
- settings.py
- mysite/urls.py
- wsgi.py
- MarkPassedEventsCommandTests
- Checklist pre-produzione
- Command
- 0003_calendar_unique_calendar_to_professor_and_more.py
- 0004_event_status_event_idx_event_active.py
- 0005_event_category_and_more.py
- cron_mark_passed_events.sh
- docker_step3.sh
- install_cron_step4.sh

## God Nodes (most connected - your core abstractions)
1. `HookSettingsTests` - 29 edges
2. `Decisioni tecniche e appunti implementativi` - 28 edges
3. `Docente` - 21 edges
4. `EventCompareTests` - 20 edges
5. `EventCategoryConstraintTests` - 17 edges
6. `ClassificaIncertiTests` - 16 edges
7. `formset_post()` - 16 edges
8. `MarkPassedEventsCommandTests` - 14 edges
9. `ShibbolethMiddlewareTests` - 13 edges
10. `Event` - 13 edges

## Surprising Connections (you probably didn't know these)
- `HookProvisioningSignalTests` --uses--> `Docente`  [INFERRED]
  TimeSheetCompiler/authentication/tests.py → TimeSheetCompiler/authentication/models.py
- `ShibbolethDevModeTests` --uses--> `Docente`  [INFERRED]
  TimeSheetCompiler/authentication/tests.py → TimeSheetCompiler/authentication/models.py
- `ShibbolethMiddlewareTests` --uses--> `Docente`  [INFERRED]
  TimeSheetCompiler/authentication/tests.py → TimeSheetCompiler/authentication/models.py
- `ClassificaIncertiTests` --uses--> `Docente`  [INFERRED]
  TimeSheetCompiler/classificationpipeline/tests.py → TimeSheetCompiler/authentication/models.py
- `EventCategoryConstraintTests` --uses--> `Docente`  [INFERRED]
  TimeSheetCompiler/classificationpipeline/tests.py → TimeSheetCompiler/authentication/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Google OAuth Login Entry Points** — timesheetcompiler_authentication_templates_authentication_index, timesheetcompiler_templates_auto_login_google, timesheetcompiler_requirements_django_allauth [INFERRED 0.85]

## Communities (43 total, 17 thin omitted)

### Community 0 - "requirements.txt (Dependency Manifest)"
Cohesion: 0.15
Nodes (14): authentication/index.html (Login Template), classificationpipeline/event_compare.html (Event Comparison Template), hooks/settings.html (Hooks Settings Template), requirements.txt (Dependency Manifest), cryptography 46.0.6, Django 6.0.3, django-allauth 65.16.1, google-api-python-client 2.193.0 (+6 more)

### Community 1 - "get_token_valido"
Cohesion: 0.23
Nodes (7): TemplateView, get_token_valido(), GetTokenValidoTests, patch, Documents open bug: a second google SocialApp row (a classic result of loading…, LoginRequiredMixin, SelectCalendarsView

### Community 2 - "Docente"
Cohesion: 0.07
Nodes (18): FormView, receiver, AuthenticationConfig, AppConfig, Provisions User/Docente from Shibboleth SP headers on first hit. The Shib…, ShibbolethAuthMiddleware, Docente, Meta (+10 more)

### Community 3 - "classificationpipeline/tests.py"
Cohesion: 0.10
Nodes (20): BaseCommand, login_required, Command, Promuove a 'passato' gli eventi che hanno superato il termine di consegna.…, Calendar, Category, Event, EventQuerySet (+12 more)

### Community 5 - "HookSettingsTests"
Cohesion: 0.07
Nodes (22): formset_post(), HookSettingsTests, override_settings, TestCase, The case the old blank extra row broke: change one saved keyword and submit,…, The stored shape is one category to many keywords; the form shows one keyword…, Casing and stray spaces are decided here and never again: every consumer…, Deleting the last rule has to be possible, and an empty formset is how it… (+14 more)

### Community 6 - "ShibbolethMiddlewareTests"
Cohesion: 0.07
Nodes (14): AvviaLoginGoogleTests, _create_google_app(), HookProvisioningSignalTests, override_settings, TestCase, SHIBBOLETH_DEV_MODE is computed at import time from DEBUG and an env var, so it…, Known behaviour: _read_headers merges as {**meta, **FAKE}, so the fake values…, The page auto-submits a POST to allauth's provider login URL, which is what… (+6 more)

### Community 7 - "._event"
Cohesion: 0.09
Nodes (11): ClassificaIncertiTests, EventQuerySetTests, EventStatusTests, Completa i kwargs di Event.objects.create per il vincolo di categoria.…, The status field is the backbone of the compilation pipeline: it is what tells…, Ingestion does not classify: everything enters unclassified and the pipeline…, The partial index condition and the future mark_passed_events job match on…, Known gap: choices is validated by forms, not by the database, and there is no… (+3 more)

### Community 8 - "Decisioni tecniche e appunti implementativi"
Cohesion: 0.06
Nodes (32): Appunto: `classificationpipeline` come cache locale eventi, ancora stub, Assorbimento dell'app `calendars` dentro `classificationpipeline`, Autenticazione pre-Shibboleth via middleware header-based, Bug latenti resi visibili dalla migrazione, Bypass locale del container Shibboleth (dev-mode), Ciclo di vita dell'evento: campo `status` e indice parziale, Conseguenze semantiche del cambio di backend sul codice futuro, Conversione di `start`/`end` di Google in `DateTimeField` (+24 more)

### Community 13 - "EventCompareTests"
Cohesion: 0.15
Nodes (7): EventCompareTests, override_settings, patch, LANGUAGE_CODE is en-us, so |date:"F" would say "September": the month name…, The POST fetches the current month's events of each selected calendar, caches…, event_compare reads request.POST.getlist('calendars'): the visible row and the…, SelectCalendarsViewTests

### Community 14 - "EventCategoryConstraintTests"
Cohesion: 0.13
Nodes (10): EventCategoryConstraintTests, `category_required_when_classified` e' l'unica cosa che impedisce a un evento…, L'ingestione non classifica: la categoria arriva dopo, da un hook, dall'LLM o…, compilato e' lo stato terminale: se ci arriva senza categoria, l'informazione…, Un evento scaduto senza essere mai stato classificato e' il caso normale, non…, Il vincolo vale a ogni scrittura, non solo alla creazione: e' quello che rende…, La sola mossa consentita per togliere una classificazione: tornare a incerto e…, mark_passed_events sposta solo lo status. La categoria resta, e il vincolo non… (+2 more)

### Community 15 - "ExpiryCutoffTests"
Cohesion: 0.14
Nodes (7): ExpiryCutoffTests, ModelTests, TestCase, (calendar, event_id) is the deduplication key: re-running ingestion for the…, The constraint is scoped to the calendar, not global: two calendars may…, (docente, calendar_id) is unique: selecting the same Google calendar twice must…, The delivery rule in one place: an event of month M expires on day 8 of month…

### Community 25 - "MarkPassedEventsCommandTests"
Cohesion: 0.22
Nodes (7): MarkPassedEventsCommandTests, The command only wires the rule to a schedule; the rule itself is tested above.…, An event old enough to be expired whatever day the suite runs., classificato means "has a classification", not "already filed": if it never…, The whole point of the passato state is not to touch history: compilato is…, The current month can never be expired: its deadline is day 8 of the next one., Idempotency is what makes a nightly schedule safe.

### Community 26 - "Checklist pre-produzione"
Cohesion: 0.25
Nodes (7): Checklist pre-produzione, Comportamenti dev-only da disattivare/verificare, Da configurare ex-novo per il deploy (non presente affatto oggi), Dati/file che non devono finire in produzione così come sono, PostgreSQL — punti aperti dopo la migrazione, Test, Variabili che oggi sono hardcoded e devono diventare env var

### Community 27 - "Command"
Cohesion: 0.33
Nodes (3): StaticfilesRunserverCommand, Command, runserver che apre /index/ nel browser all'avvio, per comodita' in sviluppo.…

## Knowledge Gaps
- **55 isolated node(s):** `Migration`, `Meta`, `Migration`, `Migration`, `Migration` (+50 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **17 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Docente` connect `Docente` to `classificationpipeline/tests.py`, `HookSettingsTests`, `ShibbolethMiddlewareTests`, `._event`, `EventCompareTests`, `EventCategoryConstraintTests`, `ExpiryCutoffTests`, `MarkPassedEventsCommandTests`?**
  _High betweenness centrality (0.249) - this node is a cross-community bridge._
- **Why does `HookSettingsTests` connect `HookSettingsTests` to `Docente`?**
  _High betweenness centrality (0.132) - this node is a cross-community bridge._
- **Why does `EventCategoryConstraintTests` connect `EventCategoryConstraintTests` to `Docente`, `classificationpipeline/tests.py`, `ExpiryCutoffTests`?**
  _High betweenness centrality (0.075) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `Docente` (e.g. with `ShibbolethAuthMiddleware` and `HookProvisioningSignalTests`) actually correct?**
  _`Docente` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Migration`, `Meta`, `Migration` to the rest of the system?**
  _55 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Docente` be split into smaller, more focused modules?**
  _Cohesion score 0.07307692307692308 - nodes in this community are weakly interconnected._
- **Should `classificationpipeline/tests.py` be split into smaller, more focused modules?**
  _Cohesion score 0.1028225806451613 - nodes in this community are weakly interconnected._