# AI Venture Team — معماری کامل V1 تا V3
## نسخه اصلاح‌شده: Dual Mode — Automatic + Manual/Learning

> هدف این سند: تعریف یک معماری عملی، قابل‌گسترش و قابل‌کنترل برای ساخت یک «تیم استارتاپی هوش مصنوعی» که بتواند مسئله را بفهمد، تخصص مناسب را انتخاب کند، کار را اجرا کند، نتیجه را ارزیابی کند و در حالت Manual/Learning هم مسیر حل مسئله را به کاربر آموزش دهد.

---

# 0. Executive Summary

سیستم دو حالت اصلی دارد:

### Automatic Mode
سیستم با حداقل دخالت کاربر، workflow را اجرا می‌کند. فقط در نقاطی که طبق policy نیاز به تأیید انسان است، متوقف می‌شود.

### Manual / Learning Mode
سیستم علاوه بر انجام کار، در checkpointهای مهم توضیح می‌دهد:
- چه چالشی پیدا شد؟
- چه گزینه‌هایی وجود داشت؟
- کدام مسیر انتخاب شد؟
- چرا این مسیر انتخاب شد؟
- چه کاری انجام شد؟
- نتیجه چه بود؟
- چه چیزی از نتیجه یاد گرفتیم؟
- قدم بعدی چیست؟

این توضیح‌ها «Learning Trace» هستند و قرار نیست chain-of-thought خام یا private reasoning مدل نمایش داده شود.

اصل معماری:

> Models are replaceable.  
> Agents are configurable.  
> Tools are permissioned.  
> Memory is selective.  
> Tasks are asynchronous.  
> Decisions are auditable.  
> Specialists are discoverable.  
> Expensive work is approval-gated.  
> Learning is explicit in Manual Mode.

---

# 1. هدف سیستم

کاربر یک ایده، مسئله یا هدف می‌دهد.

سیستم:

1. هدف را می‌فهمد.
2. مسئله را تعریف می‌کند.
3. تخصص‌های موردنیاز را تشخیص می‌دهد.
4. Agentهای مناسب را پیدا می‌کند.
5. اگر تخصص موجود نباشد، نیاز به Specialist جدید را تشخیص می‌دهد.
6. کار را به Taskهای قابل اجرا تقسیم می‌کند.
7. dependencyها را مدیریت می‌کند.
8. Taskهای مستقل را موازی اجرا می‌کند.
9. نتایج را جمع و نقد می‌کند.
10. در نقاط حساس approval می‌گیرد.
11. در صورت نیاز محصول/MVP را می‌سازد.
12. نتیجه را اندازه‌گیری می‌کند.
13. برای iteration بعدی از نتایج استفاده می‌کند.
14. در Manual/Learning Mode مسیر حل مسئله را به کاربر آموزش می‌دهد.

هدف، «تعداد زیادی Agent» نیست.

هدف:

> هر وقت مسئله‌ای مطرح شد، سیستم بتواند تشخیص دهد چه تخصصی لازم است، چه ابزار و مدلی مناسب است، تیم مناسب را تشکیل دهد، کار را انجام دهد و نتیجه قابل بررسی تحویل دهد.

---

# 2. دو حالت اجرای اصلی

## 2.1 Automatic Mode

در این حالت کاربر بیشتر نقش صاحب پروژه و تصمیم‌گیرنده را دارد.

```text
USER
 ↓
MANAGER
 ↓
PLAN
 ↓
APPROVAL GATE
 ↓
TASK GRAPH
 ↓
AGENTS
 ↓
REVIEW
 ↓
RESULT
 ↓
USER
```

Manager فقط در checkpointهای لازم توضیح مختصر می‌دهد.

کارهای کم‌ریسک می‌توانند خودکار باشند.

کارهای پرریسک، پرهزینه یا غیرقابل‌بازگشت می‌توانند approval بخواهند.

---

## 2.2 Manual / Learning Mode

در این حالت سیستم با کاربر «همراه» می‌شود.

```text
USER
 ↓
MANAGER
 ↓
CHALLENGE
 ↓
OPTIONS
 ↓
SELECTED PATH
 ↓
WHY
 ↓
ACTION
 ↓
RESULT
 ↓
LESSON
 ↓
NEXT STEP
 ↓
USER
```

هدف این حالت فقط شفافیت نیست؛ هدف، آموزش workflow و مهارت حل مسئله است.

### مثال

کاربر:
> می‌خواهم بدانم آیا این ایده SaaS ارزش ساختن دارد؟

سیستم:

```text
Challenge
هنوز نمی‌دانیم مشکل واقعی مشتری چیست.

Possible paths
1. تحقیق بازار
2. بررسی رقبا
3. بررسی شکایت کاربران
4. مصاحبه با مشتری

Selected path
3. بررسی شکایت کاربران

Why
چون در این مرحله داده اولیه مشتری نداریم و می‌خواهیم
شواهد واقعی از مشکلات موجود جمع کنیم.

Action
بررسی منابع و reviewهای مرتبط

Result
سه pain point پرتکرار پیدا شد.

Lesson
وجود pain point به تنهایی اثبات نمی‌کند که بازار مناسبی
برای محصول وجود دارد.

Next
Customer Research باید شدت و تکرار این مشکل را بررسی کند.
```

---

# 3. Learning Trace

Learning Trace یکی از اجزای رسمی سیستم است.

هر checkpoint مهم می‌تواند این ساختار را داشته باشد:

```yaml
learning_trace:
  challenge:
  context:
  possible_paths:
  selected_path:
  rationale:
  action:
  tools_used:
  evidence:
  result:
  lesson:
  next_step:
  user_question:
```

### نکته مهم

Learning Trace نباید chain-of-thought خام مدل را نمایش دهد.

به جای آن باید یک explanation قابل‌استفاده برای انسان تولید شود که:
- تصمیم را خلاصه کند.
- گزینه‌های مهم را نشان دهد.
- دلیل انتخاب را در سطح قابل‌فهم توضیح دهد.
- شواهد مرتبط را ارائه کند.
- نتیجه و قدم بعدی را مشخص کند.

---

# 4. معماری کلان

```text
                         USER
                           │
                           ▼
                    WEB / APP UI
                           │
                           ▼
                     API / GATEWAY
                           │
                           ▼
                     ORCHESTRATOR
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
      MODE ENGINE      TASK PLANNER    APPROVAL GATE
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                     AGENT DISCOVERY
                           │
                           ▼
                      TASK GRAPH
                           │
                           ▼
                     AGENT RUNTIME
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
    Research            Product          Engineering
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ▼
                        REVIEWER
                           │
                           ▼
                    RESULT / DECISION
                           │
                           ▼
                    LEARNING TRACE
                 (Manual Mode only)
                           │
                           ▼
                          USER
```

---

# 5. قرارداد مرکزی اجرای سیستم

از روز اول این قرارداد باید ثابت بماند:

```text
USER
  ↓
ORCHESTRATOR
  ↓
TASK
  ↓
AGENT
  ↓
MODEL
  ↓
TOOLS
  ↓
RESULT
  ↓
ORCHESTRATOR
```

نه:

```text
Agent → Agent → Agent → Agent → ...
```

Agentها نباید آزادانه هر Agent دیگری را صدا بزنند.

Orchestrator کنترل مرکزی را حفظ می‌کند.

---

# 6. Mode Engine

Mode Engine تعیین می‌کند سیستم چقدر با کاربر تعامل داشته باشد.

```yaml
mode:
  automatic:
    user_interaction: low
    explanation_level: concise
    checkpoint_frequency: policy_based

  manual_learning:
    user_interaction: high
    explanation_level: educational
    checkpoint_frequency: important_decisions
```

Mode Engine نباید permissionهای امنیتی را دور بزند.

یعنی Manual Mode به معنی دسترسی بیشتر نیست؛ فقط به معنی visibility و interaction بیشتر است.

---

# 7. Manager Agent

Manager مدیر تیم است.

## Input

```text
user_goal
project_context
constraints
available_agents
budget
mode
risk_policy
```

## Output

```text
problem_definition
plan
required_specialists
tasks
dependencies
risk_level
approval_request
learning_checkpoints
```

## قوانین Manager

1. مسئله را قبل از اجرا روشن کند.
2. برای کارهای مهم plan ارائه کند.
3. کار را به متخصص مناسب بدهد.
4. تضاد بین Agentها را مشخص کند.
5. بدون evidence ادعای قطعی نکند.
6. در صورت نیاز Specialist جدید درخواست کند.
7. Agent غیرضروری نسازد.
8. loop ایجاد نکند.
9. budget را رعایت کند.
10. permission policy را رعایت کند.
11. در Manual Mode تصمیم‌های مهم را به Learning Trace تبدیل کند.
12. در Automatic Mode توضیحات را تا حد لازم خلاصه نگه دارد.

---

# 8. Approval Gate

Approval Gate برای عملیات حساس است.

```text
Manager
   ↓
Plan
   ↓
Risk / Cost Check
   ↓
Approval Required?
   ├── NO → Execute
   └── YES
        ↓
      Ask User
        ├── Approve → Execute
        └── Reject/Edit → Re-plan
```

موارد معمول:

- هزینه قابل‌توجه
- deploy
- خرید
- ارسال پیام خارجی
- حذف اطلاعات
- تغییر destructive
- دسترسی حساس
- تصمیم استراتژیک مهم

---

# 9. Risk Levels

```text
LOW
 ↓
Automatic

MEDIUM
 ↓
Manager Review

HIGH
 ↓
User Approval
```

Risk level باید بر اساس policy و نوع action تعیین شود، نه بر اساس حدس Agent.

---

# 10. Agent Registry

هر Agent باید در Registry تعریف شود.

نمونه:

```yaml
name: researcher
version: 1
description: Market and evidence researcher

capabilities:
  - web_research
  - competitor_analysis
  - evidence_collection

inputs:
  - research_question

outputs:
  - findings
  - sources
  - uncertainties

tools:
  - web_search

model_role: research

permissions:
  network: true
  repo_write: false
  deploy: false

evaluation_profile:
  - source_accuracy
  - coverage
  - citation_quality
  - hallucination_rate
```

Registry باید بداند:

- Agent چیست.
- چه قابلیت‌هایی دارد.
- چه ابزارهایی دارد.
- چه model roleای دارد.
- چه permissionهایی دارد.
- چه versionی دارد.
- چه معیارهای ارزیابی دارد.

---

# 11. Agent Discovery

Manager ابتدا باید بررسی کند آیا قابلیت موردنیاز وجود دارد یا خیر.

```text
Required Capability
       ↓
Agent Registry
       ↓
Capability Match
       ├── Existing Agent → Use
       └── No suitable Agent → Specialist Request
```

قبل از ساخت Agent جدید:

1. duplicate detection
2. capability comparison
3. permission check
4. cost estimation
5. policy check

---

# 12. Agent Factory

Agent Factory مسئول ساخت Specialist جدید است.

## Specialist Specification

```text
Role
Mission
Capabilities
Inputs
Outputs
Tools
Permissions
Model Role
Quality Criteria
Safety Rules
Evaluation Plan
Lifecycle
```

Pipeline:

```text
Manager
 ↓
Need Specialist?
 ↓
Specialist Specification
 ↓
Validation
 ↓
Agent Factory
 ↓
Sandbox / Test
 ↓
Evaluation
 ↓
Registry
 ↓
Manager
```

Agent جدید نباید مستقیماً بدون تست فعال شود.

---

# 13. Agent Lifecycle

```text
DISCOVER
   ↓
SPECIFY
   ↓
CREATE
   ↓
TEST
   ↓
EVALUATE
   ↓
REGISTER
   ↓
ACTIVE
   ↓
IDLE / REUSE
   ↓
UPDATE
   ↓
DEPRECATE
```

Specialist می‌تواند temporary باشد.

مثلاً:

```text
Time-Series Specialist
```

ممکن است فقط برای یک پروژه ساخته شود.

---

# 14. جلوگیری از Agent Explosion

سیستم باید محدودیت داشته باشد:

```yaml
limits:
  max_depth:
  max_agents_per_project:
  max_spawn_per_task:
  max_cost:
  max_runtime:
```

Agent جدید فقط زمانی ایجاد شود که:

1. قابلیت جدیدی لازم باشد.
2. duplicate نباشد.
3. ابزار لازم وجود داشته باشد.
4. evaluation موفق شود.
5. budget اجازه دهد.
6. policy اجازه دهد.

---

# 15. Agentهای V1

V1 فقط:

```text
1. Manager
2. Researcher
3. Strategy / Competition
4. Customer Researcher
5. Product
6. Builder
7. Reviewer
```

بعداً:

```text
Data Scientist
ML Engineer
UX Designer
Growth
Finance
Legal
Security
DevOps
Domain Experts
```

در صورت نیاز از Agent Factory ساخته می‌شوند.

---

# 16. Research Agent

Research Pipeline:

```text
Question
 ↓
Search
 ↓
Collect Sources
 ↓
Cross-check
 ↓
Extract Evidence
 ↓
Synthesize
 ↓
Research Report
```

قانون:

```text
Fact
Inference
Hypothesis
```

باید از هم جدا باشند.

در Manual Mode:

```text
Why this research?
What are the alternatives?
Why this source?
What does the evidence actually prove?
What remains uncertain?
```

باید قابل نمایش باشد.

---

# 17. Strategy Agent

Strategy فقط نباید یک برچسب مثل «Blue Ocean» یا «Red Ocean» تولید کند.

باید ابعاد مختلف را بررسی کند:

- competition
- market saturation
- customer segments
- unmet needs
- alternatives
- differentiation
- pricing
- barriers
- potential new market
- risks

خروجی باید evidence-based باشد.

---

# 18. Customer Researcher

وظایف:

- customer profile
- pain points
- current alternatives
- unmet needs
- evidence
- assumptions
- open questions

در Manual Mode باید توضیح دهد:

```text
Pain Point چیست؟
چطور آن را پیدا کردیم؟
چه شواهدی داریم؟
چه چیزی هنوز فرضیه است؟
```

---

# 19. Product Agent

Input:

```text
Research
Strategy
Customer Findings
```

Output:

```text
Problem
Target User
Value Proposition
MVP
Features
Non-features
User Journey
Success Metrics
Validation Plan
```

در Manual Mode:

```text
Why this feature?
Why not that feature?
What assumption does it test?
What is the cheapest validation?
```

---

# 20. Builder Agent

Pipeline:

```text
Product Spec
 ↓
Technical Plan
 ↓
Architecture
 ↓
Implementation
 ↓
Tests
 ↓
Branch
 ↓
Pull Request
```

Builder می‌تواند از coding tools و sandbox استفاده کند.

در Manual Mode می‌تواند مفاهیم فنی را توضیح دهد:

```text
Problem
 ↓
Technical Choice
 ↓
Alternative
 ↓
Why this architecture?
 ↓
Implementation
 ↓
Test
```

هدف، آموزش واقعی تصمیم‌های مهندسی است.

---

# 21. Reviewer Agent

Reviewer تا حد امکان مستقل از Builder باشد.

بررسی:

- requirement coverage
- bugs
- tests
- security
- architecture
- edge cases
- maintainability

Output:

```text
PASS
```

یا:

```text
CHANGES_REQUIRED
```

Loop:

```text
Reviewer
 ↓
Fix List
 ↓
Builder
 ↓
Reviewer
```

برای جلوگیری از loop:

```text
max_iterations
escalation_to_manager
user_approval
```

---

# 22. Task Graph

Taskها به صورت DAG مدیریت شوند.

مثال:

```text
Research ──────┐
               ├──→ Product → Builder → Reviewer
Customer ──────┤
               │
Competition ───┘
```

هر Task:

```yaml
id:
owner:
input:
output:
dependencies:
status:
budget:
priority:
risk:
mode:
```

Taskهای بدون dependency می‌توانند موازی اجرا شوند.

---

# 23. Task State Machine

```text
CREATED
 ↓
READY
 ↓
RUNNING
 ↓
WAITING
 ↓
COMPLETED
 ↓
REVIEWED
 ↓
FAILED / RETRY
 ↓
ESCALATED
```

برای هر transition باید event ثبت شود.

---

# 24. Shared Memory

## Short-term State

```text
current_plan
current_context
active_tasks
recent_results
current_learning_trace
```

## Long-term Project Memory

```text
project/
├── brief
├── decisions
├── research
├── customer
├── strategy
├── product
├── technical
├── experiments
└── learning
```

Agent نباید کل حافظه را دریافت کند.

فقط context لازم retrieve شود.

---

# 25. Context Builder

```text
Agent
  ↓
Context Builder
  ├── Task Context
  ├── Relevant Project Memory
  ├── User Decisions
  ├── Required Files
  └── Previous Relevant Results
  ↓
Model
```

این کار هزینه token را پایین می‌آورد و context را مرتبط نگه می‌دارد.

---

# 26. Learning Memory

Manual Mode باید فقط نتیجه کار را ذخیره نکند.

باید lessonها را نیز ذخیره کند:

```yaml
learning_memory:
  concept:
  explanation:
  example:
  related_decision:
  source_task:
  user_question:
  mastery_signal:
```

مثلاً:

```text
Concept:
Market Saturation

Explanation:
بازار اشباع یعنی مشتریان گزینه‌های زیادی برای حل یک مشکل دارند.

Example:
اگر ۲۰ محصول مشابه با مشتریان تثبیت‌شده وجود داشته باشد،
ورود به بازار نیازمند بررسی differentiation است.

Related Decision:
انتخاب segment خاص‌تر.
```

---

# 27. Tool Layer

Agentها مستقیماً به همه‌چیز دسترسی نداشته باشند.

Tools باید permissioned باشند.

مثال:

```text
Researcher:
  web_search ✓
  repo_read ✗
  repo_write ✗
  deploy ✗

Builder:
  repo_read ✓
  repo_write ✓
  terminal ✓
  deploy ✗

DevOps:
  repo ✓
  infrastructure ✓
  deploy ✓
```

---

# 28. Security

اصل:

> Least Privilege

هر Agent فقط حداقل دسترسی لازم را داشته باشد.

برای هر Tool:

```text
allowed
denied
approval_required
```

همچنین:

- secret isolation
- audit log
- sandbox
- network policy
- file permissions
- credential boundaries

---

# 29. Model Gateway

Agentها نباید به Model ID مستقیم وابسته باشند.

```yaml
manager:
  provider: configurable
  strategy: best_reasoning

research:
  provider: configurable
  strategy: low_cost_with_tools

coding:
  provider: configurable
  strategy: coding_quality

cheap:
  provider: configurable
  strategy: lowest_cost
```

Provider می‌تواند قابل تعویض باشد.

---

# 30. Model Router

در V2:

```text
Task
 ↓
Model Router
 ├── complexity
 ├── importance
 ├── latency
 ├── cost
 ├── context size
 └── required capability
 ↓
Selected Model
```

نمونه:

```text
Simple task → cheap model
Research → tool-capable model
Architecture → strong reasoning model
Coding → coding-capable model
Classification → small model
```

---

# 31. Cost Control

```text
Project Budget
 ↓
Task Budget
 ↓
Agent Budget
 ↓
Model Calls
```

محدودیت‌ها:

```text
max_tokens
max_retries
max_subagents
max_model_cost
timeout
```

وقتی budget تمام شد:

```text
PAUSE
 ↓
Manager
 ↓
Approval
```

---

# 32. Queue / Workers

برای سبک نگه داشتن زیرساخت:

```text
API
 ↓
Queue
 ↓
Workers
```

Worker فقط وقتی job دارد اجرا شود.

Agentهای دائمی در RAM نگه داشته نشوند مگر لازم باشد.

برای کارهای سنگین:

- async jobs
- retries
- scheduled jobs
- parallel workers

---

# 33. Parallel Execution

Taskهای مستقل موازی اجرا شوند.

```text
Research ──────┐
Customer ──────┼──→ Product
Competition ───┘
```

این کار latency را کاهش می‌دهد.

---

# 34. Observability

هر Run باید trace داشته باشد:

```text
Run ID
Project ID
Task ID
Agent
Model
Tools
Tokens
Cost
Duration
Result
Errors
Mode
Approval Events
Learning Trace ID
```

Dashboard:

```text
Project
├── Agent Runs
├── Cost
├── Tokens
├── Errors
├── Approvals
├── Outputs
└── Learning History
```

---

# 35. Evaluation

هر Agent باید ارزیابی شود.

Researcher:

```text
source_accuracy
coverage
citation_quality
hallucination_rate
cost
latency
```

Builder:

```text
tests_passed
bug_rate
review_score
requirement_coverage
```

Manager:

```text
planning_quality
delegation_quality
unnecessary_agent_calls
cost
task_completion
```

Manual Mode علاوه بر کیفیت نتیجه، می‌تواند کیفیت explanation را نیز ارزیابی کند:

```text
explanation_accuracy
clarity
evidence_linkage
decision_transparency
learning_usefulness
```

---

# 36. Human-in-the-loop

سیستم autonomous است، اما تصمیم‌های مهم تحت کنترل کاربر باقی می‌مانند.

```text
LOW RISK
 ↓
Automatic

MEDIUM
 ↓
Manager Review

HIGH RISK
 ↓
User Approval
```

Manual Mode تعامل بیشتری دارد، اما permissionهای امنیتی را تغییر نمی‌دهد.

---

# 37. User Experience

صفحه شروع:

```text
What are you trying to build?

[ ایده یا مسئله خودت را بنویس ]

Mode:

○ Automatic
  Let AI handle the workflow.

○ Manual / Learning
  Work with AI and learn the reasoning process.

Depth:

○ Quick
○ Standard
○ Deep
```

بعد:

```text
Manager:

I understood your goal as...

Required specialists:
✓ Market Research
✓ Customer Research
✓ Strategy

Estimated effort:
...

Mode:
Manual / Learning

[ Review Plan ]
```

---

# 38. Manual Mode UI

در کنار هر Task:

```text
✓ Market Research

Challenge
چه چیزی را نمی‌دانستیم؟

Possible Paths
1. ...
2. ...
3. ...

Selected
...

Why?
...

Action
...

Result
...

What You Learn
...

Next Step
...

[Ask why]
[Show example]
[Explain term]
[Try it myself]
```

این UI باید کاربر را از «تماشاگر» به «یادگیرنده» تبدیل کند.

---

# 39. Interactive Learning

کاربر باید بتواند در هر checkpoint:

### Ask Why
چرا این تصمیم گرفته شد؟

### Show Example
یک مثال واقعی/ساده نشان بده.

### Explain Term
اصطلاح را ساده توضیح بده.

### Try It Myself
اجازه بده کاربر خودش تصمیم بگیرد.

مثلاً:

```text
AI:
دو مسیر داریم.

A) بررسی بازار
B) مصاحبه با مشتری

کدام را انتخاب می‌کنی؟
```

بعد از انتخاب کاربر:

```text
نتیجه انتخاب:
...

اگر گزینه دیگر را انتخاب می‌کردیم:
...

Lesson:
...
```

این بخش، Manual Mode را واقعاً آموزشی می‌کند.

---

# 40. Project State Machine

```text
IDEA
 ↓
DISCOVERY
 ↓
VALIDATION
 ↓
PRODUCT_DEFINITION
 ↓
BUILDING
 ↓
REVIEW
 ↓
LAUNCH
 ↓
MEASUREMENT
 ↓
ITERATION
```

Project در هر مرحله می‌تواند pause شود.

---

# 41. V1 Workflow

```text
USER
 ↓
SELECT MODE
 ↓
MANAGER
 ↓
PLAN
 ↓
USER APPROVAL
 ↓
Research ──────┐
Customer ──────┼── parallel
Strategy ──────┘
 ↓
Manager
 ↓
Product
 ↓
USER APPROVAL
 ↓
Builder
 ↓
Reviewer
 ↓
Fix if needed
 ↓
Final
 ↓
USER
```

در Manual Mode در checkpointهای اصلی Learning Trace نمایش داده می‌شود.

---

# 42. V2 — Self-expanding Team

بعد از پایدار شدن V1:

```text
Manager
 ↓
Expert Scout
 ↓
Does Capability Exist?
 ├── YES → Existing Agent
 └── NO
       ↓
   Agent Factory
       ↓
   Specialist
       ↓
   Evaluation
       ↓
   Registry
       ↓
   Project
```

مثال:

```text
Project needs statistics
 ↓
Data Scientist requested
 ↓
Agent Factory
 ↓
Data Scientist Specialist
 ↓
Python + SQL + Statistics
 ↓
Evaluation
 ↓
Task Execution
```

---

# 43. V3 — Autonomous Venture Team

```text
Idea Hunter
 ↓
Opportunity Discovery
 ↓
Manager
 ↓
Research / Customer / Strategy
 ↓
Validation
 ↓
Product
 ↓
Builder
 ↓
Launch
 ↓
Analytics
 ↓
Feedback
 ↓
Manager
 ↓
Iteration
```

Idea Hunter می‌تواند scheduled یا on-demand باشد.

---

# 44. Idea Hunter

وظایف:

- pain points
- trends
- market changes
- user complaints
- niches
- existing solutions
- technology opportunities

Output:

```text
Opportunity
Problem
Target Customer
Current Alternatives
Pain
Evidence
Why Now
Potential Differentiation
Open Questions
```

---

# 45. Example End-to-End

User:

> می‌خواهم یک SaaS برای شرکت‌های کوچک بسازم.

Manager:

```text
Goal:
Find and validate a SaaS opportunity.

Needed:
- Market Research
- Customer Research
- Competition
- Strategy

Plan:
1. Research market
2. Identify pain points
3. Map alternatives
4. Find underserved segments
5. Propose opportunities

Approval required.
```

در Automatic Mode:

```text
Approve
 ↓
Parallel Research
 ↓
Synthesis
 ↓
Product
 ↓
Approval
 ↓
Builder
 ↓
Reviewer
```

در Manual Mode:

```text
Approve
 ↓
Learning Checkpoint 1
"Why do we need market research?"
 ↓
Research
 ↓
Learning Checkpoint 2
"Why did we choose these sources?"
 ↓
Customer Research
 ↓
Learning Checkpoint 3
"Why this segment?"
 ↓
Strategy
 ↓
...
```

---

# 46. Failure Handling

هر Task باید بتواند fail شود.

```text
RUNNING
 ↓
ERROR
 ↓
RETRY?
 ├── YES → RETRY
 └── NO
      ↓
   ESCALATE
      ↓
   MANAGER
      ↓
USER APPROVAL / REPLAN
```

Retry باید budget و max retry داشته باشد.

---

# 47. Conflict Resolution

ممکن است Agentها نتیجه‌های متفاوت بدهند.

مثلاً:

```text
Researcher:
Market demand appears strong.

Customer Researcher:
Customer pain evidence is weak.
```

Manager نباید یکی را بی‌دلیل انتخاب کند.

باید:

1. تضاد را مشخص کند.
2. شواهد هر طرف را جمع کند.
3. uncertainty را ثبت کند.
4. در صورت نیاز research جدید ایجاد کند.
5. در Manual Mode تضاد را برای کاربر توضیح دهد.

---

# 48. Evidence Model

برای claimهای مهم:

```yaml
claim:
evidence:
source:
confidence:
type:
```

نوع claim:

```text
FACT
INFERENCE
HYPOTHESIS
```

این ساختار از تبدیل فرضیه به «حقیقت قطعی» جلوگیری می‌کند.

---

# 49. Auditability

هر تصمیم مهم باید قابل trace باشد:

```text
Decision
 ↓
Task
 ↓
Agent
 ↓
Model
 ↓
Tools
 ↓
Evidence
 ↓
Result
 ↓
Approval
```

بنابراین بعداً می‌توان فهمید:

> چرا این تصمیم گرفته شد؟

---

# 50. پیشنهادی برای Data Model

حداقل entityها:

```text
User
Project
ProjectMember
Task
TaskDependency
Agent
AgentVersion
AgentCapability
AgentTool
AgentPermission
Run
RunEvent
Approval
Decision
Evidence
LearningTrace
MemoryItem
Budget
Evaluation
Experiment
```

---

# 51. Stack پیشنهادی V1

Frontend:

```text
Next.js / React
```

Backend:

```text
Python
```

Agent Runtime:

```text
Python-based orchestration
```

Database:

```text
PostgreSQL
```

Queue:

```text
Redis-based queue
یا managed equivalent
```

Model Gateway:

```text
Internal abstraction
```

Storage:

```text
S3-compatible object storage
```

Auth:

```text
Managed authentication
```

Code Execution:

```text
Sandbox / Cloud Worker
```

Git:

```text
GitHub
```

این‌ها تنها گزینه‌های ممکن نیستند؛ هدف V1 سادگی و قابلیت تعویض است.

---

# 52. ترتیب واقعی ساخت

## Phase 1 — Foundation

- Repo
- API
- Database
- User / Project
- Task model
- Event model

## Phase 2 — Manager

- Manager prompt
- Planner
- Task graph
- Approval system
- Mode engine

## Phase 3 — Agent Registry

- Agent schema
- Capabilities
- Tools
- Permissions
- Versions

## Phase 4 — First Agents

- Research
- Customer
- Strategy
- Product

## Phase 5 — Model Gateway

- Provider abstraction
- Model routing
- Cost tracking

## Phase 6 — Builder + Reviewer

- GitHub
- Coding worker
- Tests
- PR
- Review loop

## Phase 7 — Memory

- Project state
- Decisions
- Reports
- Retrieval
- Learning memory

## Phase 8 — Manual / Learning UX

- Learning Trace
- Explain checkpoint
- Ask Why
- Show Example
- Try It Myself
- Learning history

## Phase 9 — Agent Factory

- Specialist specification
- Generation
- Sandbox
- Evaluation
- Registry

## Phase 10 — Idea Hunter

- Scheduled discovery
- Opportunity reports

## Phase 11 — Scale

- Queue workers
- Caching
- Parallel tasks
- Observability
- Rate limits
- Cost controls

---

# 53. چیزی که در V1 نباید بسازیم

برای جلوگیری از over-engineering، فعلاً لازم نیست:

- Agentهای بسیار زیاد
- Kubernetes
- microserviceهای زیاد
- vector database پیچیده
- multi-agent swarm
- autonomy کامل
- Agent-to-Agent آزاد
- Agent Factory بدون evaluation
- دسترسی مستقیم Agentها به تمام ابزارها

ابتدا workflow اصلی باید پایدار و قابل مشاهده باشد.

---

# 54. Golden Architecture

```text
                         USER
                           │
                           ▼
                    ┌──────────────┐
                    │  MODE ENGINE │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ ORCHESTRATOR │
                    │   / MANAGER  │
                    └──────┬───────┘
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
           PLAN        DISCOVERY     APPROVAL
             │             │             │
             └─────────────┼─────────────┘
                           ▼
                      TASK GRAPH
                           │
                           ▼
                     AGENT RUNTIME
                           │
                    ┌──────┼──────┐
                    ▼      ▼      ▼
                 AGENTS  TOOLS  MODELS
                    │      │      │
                    └──────┼──────┘
                           ▼
                        RESULT
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
             REVIEWER          LEARNING TRACE
                 │              (Manual Mode)
                 └─────────┬─────────┘
                           ▼
                         MEMORY
                           │
                           ▼
                         USER
```

---

# 55. اصل طلایی نهایی

سیستم باید بر این اصول ساخته شود:

```text
Models are replaceable.
Agents are configurable.
Tools are permissioned.
Memory is selective.
Tasks are asynchronous.
Tasks are observable.
Decisions are auditable.
Evidence is explicit.
Specialists are discoverable.
New specialists are evaluated.
Expensive work is approval-gated.
Risky actions are human-controlled.
Automatic Mode optimizes execution.
Manual Mode optimizes execution + learning.
```

---

# 56. تعریف نهایی محصول

محصول نهایی یک chatbot معمولی نیست.

یک **AI Venture Operating System** است.

کاربر:

```text
Goal / Idea / Problem
```

را وارد می‌کند.

سیستم:

```text
Understand
 ↓
Plan
 ↓
Discover Skills
 ↓
Assign Tasks
 ↓
Execute
 ↓
Review
 ↓
Explain
 ↓
Measure
 ↓
Iterate
```

را انجام می‌دهد.

در Automatic Mode:

> «تو هدف را بده؛ سیستم workflow را اجرا می‌کند.»

در Manual / Learning Mode:

> «تو با سیستم جلو برو؛ در هر تصمیم مهم بفهم چه مسئله‌ای وجود داشت، چه گزینه‌هایی داشتیم، چرا این مسیر انتخاب شد، چه کاری انجام شد و از آن چه چیزی یاد گرفتیم.»

و در نسخه‌های بعدی:

> «اگر تخصص لازم وجود نداشت، سیستم بتواند Specialist مناسب را تعریف، تست، ارزیابی و ثبت کند.»

---

# 57. One-Line Architecture

```text
USER → MODE → ORCHESTRATOR → TASK GRAPH → AGENT → MODEL → TOOLS → RESULT → REVIEW → MEMORY → USER
                                  ↑                         │
                                  └──── AGENT FACTORY ──────┘

                         MANUAL MODE → LEARNING TRACE
```

هدف نهایی:

> **یک تیم AI که فقط کار را انجام نمی‌دهد؛ بلکه در حالت آموزشی، روش حل مسئله را نیز به کاربر منتقل می‌کند.**
