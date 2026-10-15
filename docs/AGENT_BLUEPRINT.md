# Agent Blueprint — استاندارد اجباری ساخت Agent

> منبع: یادداشت صاحب پروژه، تصویر «Complete Agent System Architecture» (۱۴ بخش) + `docs/AGENT_PRINCIPLES.md`.
> این سند تبدیل آن معماری دست‌نویس به یک **استاندارد قابل‌اجرا** است: هر Agentی که در این factory
> ساخته می‌شود — چه با UI، چه با API — باید یک Blueprint معتبر طبق این ۱۴ بخش داشته باشد.
> اعتبارسنجی در `app/blueprint.py` است (کد قطعی، بدون فراخوانی مدل)؛ اسکیمای رسمی
> `registry/blueprint.schema.json`. مسیر تعاملی پرکردن آن، Interview است (`registry/interview/`).

## تصمیم‌های صاحب پروژه که این استاندارد باید رعایت کند

- **Stack سبک برای V1**: به‌جای MongoDB/Pinecone → **PostgreSQL + pgvector**؛ به‌جای Kubernetes →
  **Docker Compose**؛ به‌جای LangChain/LlamaIndex → **model gateway خودمان** (`app/gateway`)؛
  به‌جای Grafana/ELK → **`RunEvent` + `ModelCall` + پنل ادمین** (فعلاً کافی است).
- شش Engine (`docs/AGENT_PRINCIPLES.md`) به‌طور کامل فقط برای **Persona Agent** اجباری است.
  Agentهای متخصص فقط **Synthesis** (نتیجه‌گیری معلق) و **Memory/Log** را می‌گیرند.
- هر فراخوانی مدل از `app/gateway` رد می‌شود، با **budget check** (§53، «no free agent-to-agent calls»:
  Agentها مستقیم به هم زنگ نمی‌زنند؛ همه از orchestrator رد می‌شوند).
- چیزهایی که تصویر owner نداشت ولی برای multi-agent لازم است و اینجا اضافه شده‌اند: **Orchestrator Link**
  (بخش ۱۱)، **Approval Gate** (بخش ۱۴)، **Budget & Cost** (بخش ۹ و ۱۴)، **Evaluation** (بخش ۱۴).

هر بخش زیر: **هدف**، **فیلدهای REQUIRED**، **فیلدهای اختیاری**، و **نگاشت به چیزی که از قبل هست**.

---

## 1. Agent Core

**هدف**: هویت پایه‌ی Agent — چیست، با کدام نقش مدل کار می‌کند.

- REQUIRED: `name` (یکتا، `^[a-z][a-z0-9_-]{1,99}$`), `description`, `model_role`
  (یکی از `manager|research|coding|cheap`), `agent_type` (`persona` | `specialist`), `capabilities` (≥۱)
- Optional: `version` (پیش‌فرض ۱), `instructions` (متن سیستم‌پرامپت پایه)

**نگاشت**: همان `AgentSpec` در `app/registry.py` (فیلدهای `name/description/model_role/capabilities`)؛
`agent_type` فیلد جدیدی است که تعیین می‌کند بخش ۲ (Persona) اجباری است یا نه. مدل واقعی هرگز اینجا
هارد-کد نمی‌شود — نقش را می‌گیرد و `app/gateway` بر اساس settings به provider/model نگاشت می‌کند (Phase 5).

## 2. Persona Engine

**هدف**: تعیین می‌کند Agent «کیست» — فقط برای `agent_type: persona` اجباری است.

- REQUIRED (فقط اگر `agent_type == persona`): `persona.name`, `persona.knows` (لیست),
  `persona.does_not_understand` (لیست), `persona.patience` (`low|medium|high`), `persona.goals` (≥۱)
- Optional: `persona.tone`
- برای `specialist`: این بخش باید **غایب یا خالی** باشد — پر بودنش برای specialist خطای اعتبارسنجی است.

**نگاشت**: پیش‌نویس YAML در `docs/AGENT_PRINCIPLES.md` (بخش «پیش‌نویس Persona spec»)؛ در فاز ۳ به
`agent.instructions` تزریق می‌شود، نه یک جدول جدا.

## 3. Knowledge Boundary

**هدف**: محدودیت دانش — Agent فقط چیزی را می‌داند که از همان فاز/Persona یاد گرفته، نه دانش عمومی LLM.

- REQUIRED: `knowledge_boundary.allowed_sources` (≥۱، فقط از: `persona.knows`, `memory.phase_current`,
  `task.input`, `skills.<name>`), `knowledge_boundary.general_llm_knowledge` (باید `false` باشد)
- Optional: `knowledge_boundary.forbidden_topics`

**نگاشت**: `app/context.py::task_context` — فقط dependency summaries و decisions اخیر به مدل می‌رسد؛
system prompt صریحاً دانش عمومی را منع می‌کند (`system_prompt` + `agent.instructions`).

## 4. Action Engine

**هدف**: Agent چطور کار می‌کند — هدف‌دار، محدود، قابل ردیابی.

- REQUIRED: `action_engine.actions` (≥۱؛ برای persona: `click|choose|abandon`؛ برای specialist: نام
  capability که اجرا می‌کند)
- Optional (فقط persona): `action_engine.purposeful`, `action_engine.imperfect`, `action_engine.human`
  (پیش‌فرض هر سه `true`)

**نگاشت**: هیچ اجرای مستقیم Agent-به-Agent نیست (§53) — هر Action از orchestrator (`app/manager.py`)
رد می‌شود و به `Gateway.call` می‌رسد.

## 5. Tools & Plugins

**هدف**: چه ابزارهایی مجازند.

- REQUIRED: `tools_plugins.tools` (زیرمجموعه‌ای از `KNOWN_TOOLS` در `app/registry.py`؛ می‌تواند خالی باشد)
- Optional: `tools_plugins.plugins` (توضیح آزاد برای ابزارهای سفارشی آینده — در V1 اجرا نمی‌شود)

**نگاشت**: مستقیماً `AgentSpec.tools` + `permissions` (بخش ۱۳) — بدون permission، ابزار فعال نمی‌شود.

## 6. Memory (Short/Long Term)

**هدف**: چه چیزی بین فراخوانی‌ها می‌ماند.

- REQUIRED: `memory.short_term` (`true`, همیشه — context پنجره‌ی جاری), `memory.long_term_sources`
  (لیست؛ می‌تواند خالی باشد ولی فیلد باید حاضر باشد)
- Optional: `memory.retention_days`

**نگاشت**: کوتاه‌مدت = `app/context.py::task_context` (dependency summaries + آخرین ۵ decision)؛
بلندمدت = فاز ۷ (Project Memory) روی **PostgreSQL + pgvector** — نه Mongo، نه Pinecone.

## 7. Observation Engine

**هدف**: هر action یک reaction ثبت می‌کند — فقط برای persona کامل، برای specialist فقط لاگ ساده.

- REQUIRED: `observation.record` — برای persona: زیرمجموعه‌ای از `cognitive|emotional|behavioral` (≥۱)؛
  برای specialist: مقدار ثابت `["runevent"]`
- Optional: ندارد

**نگاشت**: `app/events.py::record_event` → `RunEvent` با `payload.reaction` برای persona.

## 8. Memory Log

**هدف**: تاریخچه‌ی قابل حسابرسی — همیشه فعال، بدون تنظیم کاربر.

- REQUIRED: هیچ (این بخش همیشه `true` است — Blueprint فقط تأیید می‌کند که Agent چیزی را دور نمی‌زند)
- Optional: `memory_log.notes`

**نگاشت**: همان `RunEvent` (بخش ۷) + `ModelCall` (هر فراخوانی مدل، بخش ۹) — Audit & Trace رایگان می‌آید،
چیزی اضافه ساخته نمی‌شود.

## 9. Data & Storage

**هدف**: کجا ذخیره می‌شود.

- REQUIRED: `data_storage.backend` — مقدار ثابت `"postgres_pgvector"` (SQLite فقط برای dev/test طبق
  CLAUDE.md؛ Mongo/Pinecone/S3 در V1 **ممنوع**، §53)
- Optional: `data_storage.file_storage` (فقط اگر واقعاً لازم — local disk، نه S3 در V1)

**نگاشت**: `app/db.py` (SQLAlchemy 2، SQLite dev / PostgreSQL prod)؛ vector search آینده روی همان
دیتابیس با pgvector، نه سرویس جدا.

## 10. External Integrations

**هدف**: چه سرویس بیرونی‌ای لمس می‌شود.

- REQUIRED: `external_integrations.services` (لیست؛ می‌تواند خالی باشد اما فیلد باید حاضر باشد — هرکدام
  باید در بخش ۱۳ Security مجوز مربوطه را داشته باشد، مثلاً `network: true`)
- Optional: `external_integrations.notes`

**نگاشت**: از طریق همان `tools` (بخش ۵) و `permissions` (بخش ۱۳) کنترل می‌شود — سرویس بیرونی بدون
permission صریح فراخوانی نمی‌شود.

## 11. Component / Orchestration Architecture

**هدف**: Agent چطور در کل سیستم جا می‌گیرد — این بخش تصویر owner را برای multi-agent کامل می‌کند.

- REQUIRED: `orchestration.via_orchestrator` (باید `true` باشد — Agentها هرگز مستقیم به هم زنگ نمی‌زنند،
  §53), `orchestration.approval_gate` (`auto|checkpoint|always` — کِی تصمیم باید پیش صاحب پروژه برود)
- Optional: `orchestration.notes`

**نگاشت**: `app/manager.py` (planner/delegator) + `/projects/{id}/tasks/{tid}/checkpoint` (Phase 2).
`approval_gate: always` یعنی هر خروجی این Agent قبل از حرکت بعدی checkpoint می‌خورد.

## 12. Tech Stack

**هدف**: مستندسازی، نه انتخاب — Stack ثابت است.

- REQUIRED: هیچ فیلد انتخابی (این بخش در Blueprint فقط echo می‌شود تا مستند بماند)
- ثابت: Python 3.11, FastAPI, SQLAlchemy 2, Pydantic 2, `app/gateway` (نه LangChain/LlamaIndex/RAG آماده)

**نگاشت**: `pyproject.toml` + CLAUDE.md.

## 13. Security & Privacy

**هدف**: مجوزها و بودجه — بدون این، Agent فعال نمی‌شود.

- REQUIRED: `security.permissions` (dict با کلیدهای `network`, `repo_write`, `deploy` — هر سه bool),
  `security.budget` (`max_cost` یا `max_tokens`، حداقل یکی؛ باید عدد مثبت باشد)
- Optional: `security.data_classification` (`public|internal|sensitive`, پیش‌فرض `internal`)

**نگاشت**: `AgentSpec.permissions` + `app/gateway/service.py` (`BudgetExceeded` → HTTP 402) +
`settings_layers` (سقف بودجه در سطح project/task).

## 14. Scalability & Reliability

**هدف**: چطور اجرا و ارزیابی می‌شود — این بخش هم برای multi-agent به تصویر owner اضافه شده.

- REQUIRED: `scalability.deployment` — مقدار ثابت `"docker_compose"` (Kubernetes ممنوع، §53),
  `scalability.evaluation` (چطور موفقیت این Agent سنجیده می‌شود — حداقل یک معیار قابل‌سنجش، مثلاً
  «۹۰٪ tasks بدون escalation»)
- Optional: `scalability.retry_policy` (پیش‌فرض همان `max_retries` سطح Task)

**نگاشت**: `docker-compose.yml` + `app/state_machine.py` (`FAILED → READY` با `max_retries`،
`→ ESCALATED`) + reviewer loop (`COMPLETED → READY` روی `CHANGES_REQUIRED`، Phase ۶).

---

## جریان کار

1. کاربر Interview را طی می‌کند (`registry/interview/*.yaml` → `GET /interview`, `POST /interview/answer`).
2. جواب‌ها یک Blueprint JSON می‌سازند مطابق `registry/blueprint.schema.json`.
3. `POST /blueprints/validate` (کد قطعی `app/blueprint.py`، بدون فراخوانی مدل) لیست موارد ناقص/نامعتبر
   را برمی‌گرداند.
4. فقط Blueprint معتبر به `AgentSpec` (بخش ۱، ۵، ۱۳) تبدیل و در `registry/agents/*.json` ثبت می‌شود.
