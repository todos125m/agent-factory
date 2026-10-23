# برنامه‌ی پیاده‌سازی کارخانه‌ی ایجنت با هدایت کاربر — gapهای تأییدشده، PRها، تست‌ها و محدودیت‌ها

> منبع: Issue #2. هر ردیف جدول gap با خواندن کد در برنچ `claude/eager-pascal-xpx6wk` (commit `b1b9a71`) تأیید شد —
> فایل و تابع ذکر شده‌اند تا قابل بازبینی باشد. معماری: `docs/HUMAN_GUIDED_FACTORY.md`؛ UI: `docs/FACTORY_UI_SPEC.md`.

## ۱. جدول gap واقعی

| # | الزام Issue | امروز در کد | gap / خطر | PR |
|---|---|---|---|---|
| G1 ✅ PR2a | کنترل دسترسی، tenant isolation، actor در هر تأیید | **هیچ احراز هویتی نیست.** UI یک کاربر ناشناس با ایمیل تصادفی می‌سازد و id را در `localStorage` نگه می‌دارد (`web/app.js::ensureUser`). هر endpoint با هر id کار می‌کند؛ `GET /inbox` کارت‌های **همه‌ی پروژه‌های همه‌ی کاربران** را برمی‌گرداند (`app/inbox.py::list_inbox`)؛ `GET /projects` بدون `owner_id` همه را | IDOR کامل؛ «actor» تأیید قابل جعل است. **مانع استقرار چندکاربره** | ۲الف (پیش‌نیاز ۲) |
| G2 ✅ PR2 | مصاحبه‌ی تطبیقی، یک سؤال اصلی، «نمی‌دانم؛ پیشنهاد بده»، بازگشت/ویرایش | ترتیب ثابت YAML (`app/interview.py::next_question`)؛ از کاربر عادی `model_role`، مجوزها و `data_classification` پرسیده می‌شود (`registry/interview/01,10`)؛ بدون «نمی‌دانم»، بدون بازگشت؛ مدل برای استخراج پاسخ آزاد طراحی شده ولی **به هیچ مسیری وصل نیست** (skill `agent-intake` فقط در docstring ارجاع شده) | کاربر عادی باید تصمیم فنی بگیرد (نقض صریح Issue) | ۲ |
| G3 ✅ PR2 | پرونده‌ی brief نسخه‌دار با `value/status/source_ref/revision` | ندارد؛ پاسخ‌ها فقط در مرورگر (PR1: `sessionStorage` همان تب) | refresh/دستگاه دیگر = از دست رفتن؛ داده‌ی حدسی و تأییدشده جدا نیستند | ۲ |
| G4 ✅ PR2 (brief) | تأیید نسخه‌دار، idempotent، رد پاسخ دیررس | تأیید برنامه و تصمیم checkpoint فقط RunEventهای `decision.*` هستند، بدون نسخه، actor، scope یا `request_id` (`app/manager.py::approve_plan/_decide`). reject همزمان با یک قفل پروژه محافظت شده (موجود و خوب) | double-submit و تأیید نسخه‌ی قدیمی قابل تشخیص نیست | ۲ |
| G5 ✅ PR3 | Blueprint پیش‌نویس، validation، تأیید طرح | validator قطعی موجود است (`app/blueprint.py`، ۱۱ بخش کد + ۳ بخش ضمنی سند)؛ Blueprint **ذخیره نمی‌شود** و تأیید ندارد | طرح قابل بازبینی/نسخه‌بندی نیست | ۳ |
| G6 ✅ PR5 | فعال‌سازی فقط بعد از ارزیابی PASS و تأیید نهایی؛ rollback | `POST /agents` ایجنت را **بلافاصله فعال** ثبت می‌کند (`Agent.active` پیش‌فرض true، `app/routers/agents.py::register_agent`)؛ `Agent.version` عدد است بدون تاریخچه | نقض «بدون evaluation PASS activation ممنوع» و §12/§13 خود ARCHITECTURE | ۵ |
| G7 ✅ PR4 | متخصص ناموجود: Task و همه‌ی وابستگی‌ها در DAG بمانند و BLOCKED شوند | `_add_plan` گامِ دارای ایجنت ناموجود را **حذف می‌کند و یال وابستگی به آن را هم دور می‌ریزد** (`app/manager.py::_add_plan`: `deps = {task_ids[d] … if task_ids[d] is not None}`)؛ فقط `specialist.requested` ثبت می‌شود | **باگ:** گام پایین‌دستی بدون پیش‌نیازش READY و اجرا می‌شود | ۴ |
| G8 ✅ PR4 | WAITING با reason؛ BLOCKED / WAITING_SPECIALIST | `TaskStatus` وضعیت BLOCKED ندارد؛ reason فقط در payload رویداد است، نه روی Task (`app/models.py::TaskStatus`) | نیاز به ستون/وضعیت جدید → نیاز به مهاجرت | ۴ |
| G9 ✅ PR4 | runtime محدود: step loop، RunSnapshot، lease/heartbeat، تکرار نکردن اثر | هر اجرای task یک فراخوانی همزمان داخل درخواست HTTP است (`run_task`)؛ worker، lease، snapshot و ToolExecution ندارد؛ ابزار فقط `web_search` سمت سرور ارائه‌دهنده | بدون سقف step و بدون بازیابی crash | ۴ |
| G10 ✅ PR5 | Builder و Reviewer/Evaluator، موارد عادی/مرزی/شکست | ساخته نشده (فاز ۶ README خالی)؛ `BenchmarkRun` فقط مسیر مدل‌ها را مقایسه می‌کند | «JSON معتبر با نتیجه‌ی ناموفق» امروز تشخیص داده نمی‌شود | ۵ |
| G11 ✅ PR6 + PR6ب | بوم workflow، typed edges، credential_ref | ندارد؛ کلیدها فقط متغیر محیطی سراسری‌اند | ✅ | ۶ |
| G12 ✅ PR7 | API بیرونی با scope، quota، نسخه‌ی pinned | ندارد | | ۷ |
| G13 ✅ PR2a | مهاجرت قابل تکرار SQLite/PostgreSQL | `create_all` + `ensure_indexes` + `ensure_column_lengths` (`app/db.py`): جدول و ایندکس جدید و عریض‌کردن؛ **افزودن ستون به جدول موجود ممکن نیست** | G8/G6 بدون مهاجرت قابل انجام نیستند | تصمیم قبل از ۲ |
| G14 | چهار محور تنظیمات؛ fa/en/tr؛ خطا با code | **بسته شد در PR1** (این برنچ) | — | ۱ ✅ |
| G15 ✅ PR8 | برچسب رویدادها برای کاربر عادی | رویدادها با نام فنی (`task.status_changed`) نمایش داده می‌شوند | زبان فنی در داشبورد کاربر عادی | ۲ (UI) |
| G16 ✅ PR8 | ایجنت‌های ثبت‌شده: توضیح چندزبانه | `registry/agents/*.json` فقط انگلیسی (محتوا، نه UI) | | ۳ |

موجوداتی که **reuse** می‌شوند و نباید دوباره ساخته شوند: Gateway و BudgetHold (بودجه‌ی worst-case)، سیاست توقف d16 و
قفل پروژه (`app/pause.py`)، گارد شواهد FACT/INFERENCE (`_apply_evidence_guard`)، validator Blueprint، لایه‌های تنظیمات،
`StorableIn`/`limit()` برای حدود PostgreSQL، RunEvent/ModelCall برای ممیزی.

## ۲. برنامه‌ی PR

| PR | محدوده | شرط پذیرش | وضعیت |
|---|---|---|---|
| ۱ | همسوسازی اسناد، جدول gap، تنظیمات و زیرساخت i18n | چهار محور مستقل؛ fa/en/tr ذخیره و تغییر بدون reset | **این PR** |
| ۲الف | احراز هویت حداقلی و مالکیت (G1) + Alembic (G13) | هر endpoint فقط داده‌ی مالک را می‌دهد؛ تست tenant isolation؛ actor واقعی | **انجام شد** (d33، d34) |
| ۲ | session پایدار، brief نسخه‌دار، تأیید، مصاحبه‌ی تطبیقی (G2–G4، G15) | سؤال مرتبط؛ refresh/resume؛ رد پاسخ قدیمی و double submit | **انجام شد** (برچسب رویدادها G15: هنوز) |
| ۳ | طراحی Blueprint و نمای شاخه‌ای (G5، G16) | ۱۴ بخش validate؛ تأیید/ویرایش؛ نگاشت UI واقعی بدون مجوز ضمنی | **انجام شد** (G16 توضیح سه‌زبانه‌ی ایجنت‌های registry: هنوز) |
| ۴ | DAG متخصص ناموجود و runtime محدود یک نقش (G7–G9) | وابستگی حفظ؛ action/observation؛ سقف هزینه و توقف قابل اثبات | **انجام شد** (ابزار پیاده‌شده: فقط `memory_search`؛ بقیه رد با `tool_unavailable` تا PR6) |
| ۵ | Builder/Evaluator محدود و activation نسخه‌دار (G6، G10) | PASS واقعی و تأیید نهایی؛ اصلاح bounded؛ rollback | **انجام شد** (d36 داور مدل، d37 مسیر مستقیم ادمین می‌ماند، d38 سقف ۳ دور) |
| ۶ | workflow typed edges و اتصال provider/tool (G11) | ناسازگاری/چرخه/مالکیت رد؛ Save جدا از Run؛ credentials امن | |
| ۷ | API بیرونی در صورت آمادگی (G12) | scope، quota، version pin، checkpoint؛ در غیر این صورت «در دسترس نیست» | **انجام شد** (d42 کلید = تأیید دائمی، d43 سقف روزانه + بودجه‌ی هر اجرا، d44 فقط ایجنت ساخته و فعال‌شده‌ی مالک) |

هر PR مسیر قابل استفاده تحویل می‌دهد (backend + UI + خطاهای همان مسیر)، branch از برنچ مبنا و PR به همان برنچ؛ merge،
deploy و افزایش مجوزهای محصول مجوز جداگانه می‌خواهند.

## ۳. PR1 — آنچه انجام شد و شواهد

- `app/preferences.py`، مدل `UserPreference`، `GET/PATCH /users/{id}/preferences`.
- `app/settings_layers.py`: `interaction_policy` (مشتق از mode)، `max_interaction_policy` (سقف، fail-closed)،
  `quality_target`، `output_locale`؛ اعتبارسنجی در `SettingsLayerIn`.
- `app/manager.py::create_checkpoint`: سیاست `step_by_step` تصمیم خودکار را متوقف می‌کند (فقط محدودکننده).
- زبان پاسخ مدل در plan/chat/task (`Reply language: …`، فقط در محتوای کاربر).
- `app/errors.py` + handlerها: `code`/`params` روی هر خطا؛ همه‌ی routerها کدگذاری شدند.
- مصاحبه‌ی سه‌زبانه: `registry/interview/*.yaml` با `{fa,en,tr}`؛ `?locale=` و `locale` در بدنه؛ رفع ۵۰۰ روی بدنه‌ی
  نادرست (`answers` غیرشیء، `target` غیررشته).
- `web/i18n.js` (۳۱۲ کلید × ۳ زبان، fallback انگلیسی)، `web/app.js` بدون متن ثابت، انتخاب زبان در اولین ورود و نوار بالا،
  کارت «ترجیح‌های من»، فیلدهای سیاست/کیفیت/زبان خروجی در تنظیمات، فرم کوتاه برای سطح عمومی، JSON فقط با نمایش کد، CSS
  منطقی برای LTR/RTL.
- تست‌ها: `test_preferences.py`، `test_interaction_policy.py`، `test_error_codes.py`، `test_i18n.py` و تست‌های جدید
  `test_interview.py`؛ جدول‌های ممیزی `test_column_limits.py` و `test_settings_validation.py` به‌روز شدند.

## ۴. تست‌های ضروری بقیه‌ی PRها

از Issue (هر کدام در PR مربوط، با تست خودکار): سناریوی «ایجنت بررسی تیکت پشتیبانی» سرتاسری؛ سه سطح → brief یکسان؛
پاسخ مبهم/متناقض → سؤال مرتبط و بدون brief approval طراحی پیش نمی‌رود؛ resume از checkpoint؛ اصلاح مسئله فقط
descendants را باطل کند؛ دو تب/double submit/پاسخ قدیمی؛ متخصص ناموجود downstream را blocked نگه دارد؛ deny/pause/
بودجه پیش از ابزار/مدل/نوشتن؛ JSON معتبر با نتیجه‌ی ناموفق PASS نشود؛ موارد عادی/مرزی/شکست؛ بدون PASS و تأیید نهایی
activation ممنوع و rollback؛ نبود secret در export/log؛ mapping سازگار/ناسازگار؛ worker crash بدون تکرار اثر؛ تغییر
مدل/منبع و ابطال ارزیابی؛ tenant isolation؛ تغییر زبان وسط مصاحبه (**PR1 ✅**)؛ مخفی بودن کد بدون حذف دلیل (**PR1 ✅**).
smoke test مدل واقعی فقط با تنظیمات صریح و بودجه‌ی محدود.

## ۵. تصمیم‌های باز برای مالک

1. **احراز هویت (G1):** پیشنهاد: ورود حداقلی (لینک/توکن نشست سمت سرور، بدون رمز در UI) قبل از PR2، چون actor تأییدها
   بدون آن معنا ندارد. جایگزین: ادامه‌ی تک‌کاربره روی localhost با برچسب صریح «فقط محلی».
2. **مهاجرت (G13):** پیشنهاد: Alembic با baseline از schema فعلی و تست روی SQLite و PostgreSQL واقعی (کلاستر موقت).
   جایگزین: ادامه‌ی الگوی `ensure_*` برای افزودن ستون nullable — ساده‌تر ولی بدون تاریخچه.
3. **تم UI:** A / B / C (`FACTORY_UI_SPEC.md` §۵). تا انتخاب، prototype بوم با A ساخته می‌شود.

## ۶. محدودیت‌های شناخته‌شده‌ی PR1

- سیاست تعامل فقط تصمیم خودکار checkpoint را محدود می‌کند؛ `milestone` و `delegated_bounded` تا PR4 رفتار یکسان دارند.
- `quality_target` ذخیره و resolve می‌شود ولی تا ارزیاب (PR5) مصرف‌کننده‌ای ندارد.
- ترجیح‌ها مثل بقیه‌ی API بدون احراز هویت‌اند (G1): هر کس id را بداند می‌تواند بخواند/تغییر دهد.
- متن تایپ‌شده‌ی ذخیره‌نشده در یک فرم با تغییر زبان پاک می‌شود (پاسخ‌های مصاحبه نه).
- نام رویدادها و توضیح ایجنت‌های registry ترجمه نشده‌اند (G15، G16).
- هیچ فراخوانی مدل واقعی موفقی در این PR انجام نشد (یک تلاش در تست مرورگر با «حساب Claude» در CLI وارد‌نشده رد شد،
  هزینه صفر)؛ زبان پاسخ فقط تا سطح متن ارسالی به مدل (FakeProvider) تست شده است، نه رفتار یک مدل واقعی.
