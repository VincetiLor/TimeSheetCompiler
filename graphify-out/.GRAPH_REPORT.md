# Graph Report - TimeSheetCompiler  (2026-08-21)

## Corpus Check
- Corpus is ~2,082 words - fits in a single context window. You may not need a graph.

## Summary
- 104 nodes · 75 edges · 38 communities (19 shown, 19 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 6 edges (avg confidence: 0.88)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Templates & Dependencies
- Google Token Refresh (calendars)
- Hook Settings App
- Classification Models
- Docente Model
- Authentication AppConfig
- Calendars AppConfig
- ClassificationPipeline AppConfig
- Hooks AppConfig
- manage.py Entrypoint
- Auth Migration 0001
- Calendars Migration 0001
- Calendars Migration 0002
- Calendar Selection JS
- ClassificationPipeline Migration 0001
- ClassificationPipeline Migration 0002
- Hooks Migration 0001
- ASGI Entrypoint
- Django Settings
- Root URLConf
- WSGI Entrypoint
- Calendar Selection addCalendar JS

## God Nodes (most connected - your core abstractions)
1. `requirements.txt (Dependency Manifest)` - 8 edges
2. `Hook_settings` - 5 edges
3. `get_token_valido()` - 4 edges
4. `SelectCalendarsView` - 4 edges
5. `Hook` - 4 edges
6. `templates/index.html (Base Template)` - 4 edges
7. `Docente` - 3 edges
8. `authentication/index.html (Login Template)` - 3 edges
9. `calendars/eventi_calendars.html (Calendar Selection Template)` - 3 edges
10. `django-allauth 65.16.1` - 3 edges

## Surprising Connections (you probably didn't know these)
- `authentication/index.html (Login Template)` --semantically_similar_to--> `templates/auto_login_google.html (Auto-Submit Google Login Template)`  [INFERRED] [semantically similar]
  TimeSheetCompiler/authentication/templates/authentication/index.html → TimeSheetCompiler/templates/auto_login_google.html
- `Hook_settings` --uses--> `Hook`  [INFERRED]
  TimeSheetCompiler/hooks/views.py → TimeSheetCompiler/hooks/models.py
- `authentication/index.html (Login Template)` --conceptually_related_to--> `django-allauth 65.16.1`  [INFERRED]
  TimeSheetCompiler/authentication/templates/authentication/index.html → TimeSheetCompiler/requirements.txt
- `calendars/eventi_calendars.html (Calendar Selection Template)` --references--> `classificationpipeline/event_compare.html (Event Comparison Template)`  [INFERRED]
  TimeSheetCompiler/calendars/templates/calendars/eventi_calendars.html → TimeSheetCompiler/classificationpipeline/templates/classificationpipeline/event_compare.html
- `calendars/eventi_calendars.html (Calendar Selection Template)` --conceptually_related_to--> `google-api-python-client 2.193.0`  [INFERRED]
  TimeSheetCompiler/calendars/templates/calendars/eventi_calendars.html → TimeSheetCompiler/requirements.txt

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Templates Extending Base index.html** — timesheetcompiler_authentication_templates_authentication_index, timesheetcompiler_calendars_templates_calendars_eventi_calendars, timesheetcompiler_classificationpipeline_templates_classificationpipeline_event_compare, timesheetcompiler_hooks_templates_hooks_settings, timesheetcompiler_templates_index [INFERRED 0.85]
- **Google OAuth Login Entry Points** — timesheetcompiler_authentication_templates_authentication_index, timesheetcompiler_templates_auto_login_google, timesheetcompiler_requirements_django_allauth [INFERRED 0.85]
- **Calendar Selection to Classification Flow** — timesheetcompiler_calendars_templates_calendars_eventi_calendars, timesheetcompiler_classificationpipeline_templates_classificationpipeline_event_compare, timesheetcompiler_requirements_google_api_python_client [INFERRED 0.75]

## Communities (38 total, 19 thin omitted)

### Community 0 - "Templates & Dependencies"
Cohesion: 0.16
Nodes (15): authentication/index.html (Login Template), calendars/eventi_calendars.html (Calendar Selection Template), classificationpipeline/event_compare.html (Event Comparison Template), hooks/settings.html (Hooks Settings Template), requirements.txt (Dependency Manifest), cryptography 46.0.6, Django 6.0.3, django-allauth 65.16.1 (+7 more)

### Community 1 - "Google Token Refresh (calendars)"
Cohesion: 0.22
Nodes (3): TemplateView, get_token_valido(), SelectCalendarsView

### Community 2 - "Hook Settings App"
Cohesion: 0.24
Nodes (3): Hook, Hook_settings, UpdateView

## Knowledge Gaps
- **17 isolated node(s):** `Migration`, `Meta`, `Migration`, `Migration`, `Migration` (+12 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **19 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Hook_settings` connect `Hook Settings App` to `Google Token Refresh (calendars)`?**
  _High betweenness centrality (0.008) - this node is a cross-community bridge._
- **What connects `Migration`, `Meta`, `Migration` to the rest of the system?**
  _17 weakly-connected nodes found - possible documentation gaps or missing edges._