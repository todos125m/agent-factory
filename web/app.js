(function(){
'use strict';

/* ============================== constants ============================== */

var STAGES = ['IDEA','DISCOVERY','VALIDATION','PRODUCT_DEFINITION','BUILDING','REVIEW','LAUNCH','MEASUREMENT','ITERATION'];
var STAGE_FA = {IDEA:'ایده',DISCOVERY:'کشف',VALIDATION:'اعتبارسنجی',PRODUCT_DEFINITION:'تعریف محصول',BUILDING:'ساخت',
  REVIEW:'بازبینی',LAUNCH:'راه‌اندازی',MEASUREMENT:'سنجش',ITERATION:'تکرار'};
var MODE_FA = {automatic:'خودکار', manual_learning:'دستی / آموزشی'};
var DEPTH_FA = {quick:'سریع', standard:'استاندارد', deep:'عمیق'};
var STATUS_FA = {CREATED:'ایجادشده',READY:'آماده',RUNNING:'در حال اجرا',WAITING:'در انتظار',COMPLETED:'تمام‌شده',
  REVIEWED:'بازبینی‌شده',FAILED:'ناموفق',ESCALATED:'ارجاع‌شده',CANCELLED:'لغوشده'};
var RISK_FA = {low:'کم', medium:'متوسط', high:'پرریسک'};
var APPROVAL_FA = {auto:'خودکار', manager:'مدیر', user:'کاربر'};
var MODEL_ROLES = ['manager','research','coding','cheap'];
var ROLE_FA = {manager:'مدیر', research:'پژوهش', coding:'کدنویسی', cheap:'ارزان'};
var PROVIDERS = ['anthropic','claude_account','ollama','openai'];
var MODEL_ACCESS_FA = {api_key:'کلید API', claude_account:'حساب Claude خودم', ollama:'Ollama (محلی)'};
var MEMORY_CATEGORY_FA = {brief:'بریف', decisions:'تصمیم‌ها', research:'پژوهش', customer:'مشتری',
  strategy:'استراتژی', product:'محصول', technical:'فنی', experiments:'آزمایش‌ها', learning:'یادگیری'};

/* ============================== api client ============================== */

var ERR_MAP = {402:'بودجه تمام شد.', 502:'مدل در دسترس نیست.'};

function api(path, opts){
  opts = opts || {};
  var init = {method: opts.method || 'GET', headers:{}};
  if (opts.json !== undefined){
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.json);
  }
  return fetch(path, init).then(function(res){
    return res.text().then(function(text){
      var data = null;
      try { data = text ? JSON.parse(text) : null; } catch(e){}
      if (!res.ok){
        var msg = ERR_MAP[res.status] || (data && data.detail ? String(data.detail) : ('خطا (' + res.status + ')'));
        var err = new Error(msg);
        err.status = res.status;
        throw err;
      }
      return data;
    });
  });
}

/* ============================== dom helpers ============================== */

function el(tag, props){
  var node = document.createElement(tag);
  var children = Array.prototype.slice.call(arguments, 2);
  if (props) for (var k in props){
    if (k === 'class') node.className = props[k];
    else if (k === 'text') node.textContent = props[k];
    else if (k.indexOf('on') === 0 && typeof props[k] === 'function') node.addEventListener(k.slice(2), props[k]);
    else if (props[k] !== null && props[k] !== undefined) node.setAttribute(k, props[k]);
  }
  children.forEach(function(c){
    if (c === null || c === undefined) return;
    if (Array.isArray(c)) c.forEach(function(cc){ if (cc) node.appendChild(cc); });
    else if (typeof c === 'string') node.appendChild(document.createTextNode(c));
    else node.appendChild(c);
  });
  return node;
}
function clear(node){ while (node.firstChild) node.removeChild(node.firstChild); }
function fmtMoney(n){ return '$' + (Math.round((n||0) * 10000) / 10000).toFixed(4); }
function fmtNum(n){ return new Intl.NumberFormat('fa-IR').format(n||0); }
function fmtTime(iso){
  try { return new Date(iso).toLocaleString('fa-IR', {dateStyle:'short', timeStyle:'short'}); }
  catch(e){ return iso; }
}
function statusChip(status){ return el('span', {class:'chip status-' + status, text: STATUS_FA[status] || status}); }
function riskChip(risk){
  if (risk === 'low') return null;
  return el('span', {class:'chip risk-' + risk, text: RISK_FA[risk] || risk});
}
function loadingBox(text){
  return el('div', {class:'loading-state'}, el('span', {class:'spin'}), el('span', {text: text || 'در حال بارگذاری…'}));
}
function errorBox(message, retry){
  var b = el('div', {class:'error-state'}, el('div', {class:'ic', text:'⚠️'}), el('b', {text:'مشکلی پیش آمد'}),
    el('span', {text: message || 'خطا در ارتباط با سرور'}));
  if (retry) b.appendChild(el('button', {class:'btn sm', style:'margin-top:6px', onclick: retry}, 'دوباره امتحان کن'));
  return b;
}
function emptyBox(icon, title, sub){
  return el('div', {class:'empty-state'}, el('div', {class:'ic', text: icon}), el('b', {text: title}),
    sub ? el('span', {text: sub}) : null);
}

/* ============================== app state ============================== */

var STATE = { userId: null, workspaces: [], projects: [] };

function ensureUser(){
  var id = localStorage.getItem('af_user_id');
  if (id) { STATE.userId = Number(id); return Promise.resolve(STATE.userId); }
  var email = 'owner-' + Math.random().toString(36).slice(2, 10) + '@agent-factory.local';
  return api('/users', {method:'POST', json:{email: email, name:'مالک'}}).then(function(u){
    localStorage.setItem('af_user_id', u.id);
    STATE.userId = u.id;
    return u.id;
  });
}

function fetchWorkspaces(){ return api('/workspaces').then(function(ws){ STATE.workspaces = ws; return ws; }); }
function fetchProjects(){ return api('/projects?owner_id=' + STATE.userId).then(function(ps){ STATE.projects = ps; return ps; }); }

/* ============================== router ============================== */

var PAGES = ['dashboard','inbox','projects','agents','benchmark','settings','observability'];
var root;

function setNav(page){
  document.querySelectorAll('.nav-item').forEach(function(b){
    b.classList.toggle('active', b.dataset.page === page);
  });
}

function refreshInboxBadge(){
  api('/inbox').then(function(items){
    var badge = document.getElementById('inbox-badge');
    if (!badge) return;
    if (items.length){ badge.hidden = false; badge.textContent = items.length > 99 ? '99+' : String(items.length); }
    else badge.hidden = true;
  }).catch(function(){});
}

function route(){
  var hash = (location.hash || '#dashboard').slice(1);
  var parts = hash.split('/');
  var page = parts[0];
  if (PAGES.indexOf(page) === -1) page = 'dashboard';
  setNav(page);
  clear(root);
  refreshInboxBadge();
  if (page === 'dashboard') renderDashboard(root);
  else if (page === 'inbox') renderInbox(root);
  else if (page === 'projects' && parts[1]) renderProjectDetail(root, Number(parts[1]));
  else if (page === 'projects') renderProjectsList(root);
  else if (page === 'agents' && parts[1] === 'new') renderAgentIntake(root);
  else if (page === 'agents' && parts[1]) renderAgentDetail(root, decodeURIComponent(parts[1]));
  else if (page === 'agents') renderAgentsList(root);
  else if (page === 'benchmark') renderBenchmark(root);
  else if (page === 'settings') renderSettings(root);
  else if (page === 'observability') renderObservability(root);
}

function go(hash){ location.hash = hash; }

/* ============================== feedback (training) ============================== */

function feedbackWidget(projectId, agent, taskId){
  var wrap = el('div', {class:'feedback-row'});
  var note = el('input', {type:'text', placeholder:'یادداشت (اختیاری)', style:'flex:1 1 160px'});
  var status = el('span', {class:'muted'});
  var up = el('button', {class:'thumb', type:'button', text:'👍'});
  var down = el('button', {class:'thumb', type:'button', text:'👎'});
  function submit(rating){
    up.classList.toggle('active-up', rating === 'up');
    down.classList.toggle('active-down', rating === 'down');
    status.textContent = '…';
    api('/feedback', {method:'POST', json:{
      project_id: projectId, task_id: taskId, agent: agent, rating: rating, note: note.value.trim() || null
    }}).then(function(){ status.textContent = 'ثبت شد.'; }).catch(function(e){ status.textContent = e.message; });
  }
  up.addEventListener('click', function(){ submit('up'); });
  down.addEventListener('click', function(){ submit('down'); });
  wrap.appendChild(el('span', {class:'muted', text:'این پاسخ به‌دردخور بود؟'}));
  wrap.appendChild(up); wrap.appendChild(down); wrap.appendChild(note); wrap.appendChild(status);
  return wrap;
}

/* ============================== inbox ============================== */

function renderInbox(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:'صندوق تصمیم‌ها'})));
  var body = el('div', {class:'stack'});
  root.appendChild(body);

  function load(){
    clear(body); body.appendChild(loadingBox());
    api('/inbox').then(function(items){
      clear(body);
      refreshInboxBadge();
      if (!items.length){
        body.appendChild(emptyBox('📥', 'چیزی منتظر تو نیست', 'وقتی برنامه‌ای یا نقطه‌ی تصمیمی منتظر بماند، اینجا نشان داده می‌شود.'));
        return;
      }
      items.forEach(function(it){ body.appendChild(inboxCard(it, load)); });
    }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message, load)); });
  }
  load();
}

function inboxCard(item, onDone){
  var card = el('div', {class:'card stack inbox-card'});
  card.appendChild(el('div', {class:'row', style:'justify-content:space-between'},
    el('span', {class:'kind', text: item.kind === 'plan' ? 'برنامه در انتظار تأیید' : 'نقطه‌ی تصمیم'}),
    el('button', {class:'chip', style:'cursor:pointer;border:0', onclick:function(){ go('projects/' + item.project_id); }}, item.project_title)));
  card.appendChild(el('p', {}, el('b', {text: item.kind === 'plan' ? 'فهم مدیر: ' : 'چالش: '}), item.challenge));

  if (item.kind === 'plan'){
    var ol = el('div', {class:'stack', style:'gap:6px'});
    (item.options || []).forEach(function(o){
      ol.appendChild(el('div', {class:'row', style:'justify-content:space-between'},
        el('span', {text:o.title}), el('span', {class:'row'}, el('span', {class:'chip', text:o.owner || '?'}), riskChip(o.risk))));
    });
    card.appendChild(ol);
    var fb = el('textarea', {placeholder:'اگر برنامه را قبول نداری، بنویس چه چیزی عوض شود'});
    var status = el('p', {class:'err muted'});
    var approveBtn = el('button', {class:'btn', text:'تأیید برنامه'});
    var rejectBtn = el('button', {class:'btn ghost', text:'رد و بازنویسی'});
    approveBtn.addEventListener('click', function(){
      approveBtn.disabled = true;
      api('/projects/' + item.project_id + '/plan/approve', {method:'POST'}).then(onDone)
        .catch(function(e){ status.textContent = e.message; approveBtn.disabled = false; });
    });
    rejectBtn.addEventListener('click', function(){
      var f = fb.value.trim();
      if (!f){ status.textContent = 'بنویس چه چیزی باید عوض شود.'; return; }
      rejectBtn.disabled = true;
      api('/projects/' + item.project_id + '/plan/reject', {method:'POST', json:{feedback:f}}).then(onDone)
        .catch(function(e){ status.textContent = e.message; rejectBtn.disabled = false; });
    });
    card.appendChild(el('label', {class:'f'}, 'بازخورد', fb));
    card.appendChild(el('div', {class:'btn-row'}, approveBtn, rejectBtn));
    card.appendChild(status);
  } else {
    var picked = item.recommended || 0;
    var group = el('div', {class:'stack', style:'gap:8px', role:'radiogroup'});
    (item.options || []).forEach(function(o, i){
      var opt = el('button', {class:'opt', role:'radio', 'aria-checked': String(i === picked)},
        el('span', {}, el('b', {}, o.title, i === item.recommended ? el('span', {class:'rec', text:'پیشنهاد مدیر'}) : null),
          el('small', {text:o.tradeoff})));
      opt.addEventListener('click', function(){
        picked = i;
        group.querySelectorAll('.opt').forEach(function(x, xi){ x.setAttribute('aria-checked', String(xi === i)); });
      });
      group.appendChild(opt);
    });
    card.appendChild(group);
    if (item.why) card.appendChild(el('p', {class:'muted'}, el('b', {text:'چرا: '}), item.why));
    var note = el('textarea', {placeholder:'یادداشت (اختیاری)'});
    card.appendChild(el('label', {class:'f'}, 'یادداشت', note));
    var status2 = el('p', {class:'err muted'});
    var decideBtn = el('button', {class:'btn', text:'تأیید و اجرا'});
    decideBtn.addEventListener('click', function(){
      decideBtn.disabled = true;
      api('/projects/' + item.project_id + '/tasks/' + item.task_id + '/decide', {method:'POST', json:{option:picked, note: note.value.trim() || null}})
        .then(onDone).catch(function(e){ status2.textContent = e.message; decideBtn.disabled = false; });
    });
    card.appendChild(el('div', {class:'btn-row'}, decideBtn));
    card.appendChild(status2);
  }
  return card;
}

/* ============================== dashboard ============================== */

function renderDashboard(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:'داشبورد'})));
  var body = el('div', {class:'stack'});
  root.appendChild(body);
  body.appendChild(loadingBox());

  fetchProjects().then(function(projects){
    return Promise.all(projects.map(function(p){
      return api('/projects/' + p.id + '/usage').catch(function(){ return null; });
    })).then(function(usages){
      return Promise.all(projects.slice(0, 6).map(function(p){
        return api('/projects/' + p.id + '/events').then(function(evs){
          return evs.slice(-5).map(function(e){ e._project = p; return e; });
        }).catch(function(){ return []; });
      })).then(function(eventLists){
        var allEvents = [].concat.apply([], eventLists).sort(function(a,b){ return b.id - a.id; }).slice(0, 8);
        renderDashboardBody(body, projects, usages, allEvents);
      });
    });
  }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message, function(){ clear(root); renderDashboard(root); })); });
}

function renderDashboardBody(body, projects, usages, events){
  clear(body);
  if (!projects.length){
    body.appendChild(emptyBox('📊', 'هنوز پروژه‌ای نساخته‌ای', 'وقتی پروژه‌ای بسازی، خلاصه‌ی آن اینجا نمایش داده می‌شود.'));
    body.appendChild(el('button', {class:'btn', onclick:function(){ go('projects'); }}, '+ ساخت اولین پروژه'));
    return;
  }
  var totalCost = 0, totalBudget = 0;
  usages.forEach(function(u){ if (u){ totalCost += u.cost_usd; totalBudget += u.budget_usd; } });

  var stats = el('div', {class:'grid grid-3'},
    el('div', {class:'card stat'}, el('b', {text: fmtNum(projects.length)}), el('span', {text:'تعداد پروژه‌ها'})),
    el('div', {class:'card stat'}, el('b', {text: fmtMoney(totalCost)}), el('span', {text:'مجموع هزینه‌ی مدل‌ها'})),
    el('div', {class:'card stat'}, el('b', {text: fmtMoney(totalBudget)}), el('span', {text:'مجموع بودجه'}))
  );
  body.appendChild(stats);

  var recent = el('div', {class:'card stack'}, el('h3', {text:'پروژه‌های اخیر'}));
  var list = el('div', {class:'list'});
  projects.slice().reverse().slice(0, 5).forEach(function(p){
    list.appendChild(el('button', {class:'list-item', onclick:function(){ go('projects/' + p.id); }},
      el('div', {class:'top'}, el('b', {text:p.title}), el('span', {class:'chip', text: STAGE_FA[p.stage] || p.stage})),
      el('span', {class:'muted', text: p.goal})
    ));
  });
  recent.appendChild(list);
  body.appendChild(recent);

  var evCard = el('div', {class:'card stack'}, el('h3', {text:'رویدادهای اخیر'}));
  if (!events.length) evCard.appendChild(el('p', {class:'muted', text:'رویدادی ثبت نشده.'}));
  else events.forEach(function(e){ evCard.appendChild(eventRow(e, e._project ? e._project.title : null)); });
  body.appendChild(evCard);
}

function eventRow(e, projectLabel){
  return el('div', {class:'event-row'},
    el('time', {text: fmtTime(e.created_at)}),
    el('div', {class:'stack', style:'gap:2px'},
      el('div', {}, el('code', {text: e.type}), projectLabel ? el('span', {class:'muted', text:' · ' + projectLabel}) : null)
    )
  );
}

/* ============================== projects list ============================== */

function renderProjectsList(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:'پروژه‌ها'}),
    el('div', {class:'spacer', style:'flex:1'}),
    el('button', {class:'btn sm', id:'new-project-btn'}, '+ پروژه‌ی جدید')));
  var formHolder = el('div');
  var body = el('div', {class:'stack'});
  root.appendChild(formHolder);
  root.appendChild(body);
  body.appendChild(loadingBox());

  root.querySelector('#new-project-btn').addEventListener('click', function(){
    if (formHolder.firstChild) { clear(formHolder); return; }
    clear(formHolder);
    formHolder.appendChild(projectCreateForm(function(){ clear(formHolder); load(); }));
  });

  function load(){
    clear(body); body.appendChild(loadingBox());
    Promise.all([fetchProjects(), fetchWorkspaces()]).then(function(){
      clear(body);
      if (!STATE.projects.length){
        body.appendChild(emptyBox('📁', 'هنوز پروژه‌ای نیست', 'با دکمه‌ی «پروژه‌ی جدید» شروع کن.'));
        return;
      }
      var list = el('div', {class:'list'});
      STATE.projects.slice().reverse().forEach(function(p){
        list.appendChild(el('button', {class:'list-item', onclick:function(){ go('projects/' + p.id); }},
          el('div', {class:'top'}, el('b', {text:p.title}), el('span', {class:'chip', text: STAGE_FA[p.stage] || p.stage})),
          el('span', {class:'muted', text:p.goal}),
          el('div', {class:'row'}, el('span', {class:'chip', text: MODE_FA[p.mode] || p.mode}),
            p.paused ? el('span', {class:'chip', text:'متوقف‌شده'}) : null)
        ));
      });
      body.appendChild(list);
    }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message, load)); });
  }
  load();
}

function projectCreateForm(onDone){
  var title = el('input', {type:'text', placeholder:'مثلاً: اعتبارسنجی SaaS برای کسب‌وکارهای کوچک'});
  var goal = el('textarea', {placeholder:'هدف پروژه را توضیح بده'});
  var mode = el('select', {},
    el('option', {value:'automatic'}, 'خودکار'),
    el('option', {value:'manual_learning'}, 'دستی / آموزشی'));
  var budget = el('input', {type:'number', min:'0', step:'0.1', placeholder:'اختیاری — پیش‌فرض سراسری استفاده می‌شود'});
  var ws = el('select', {}, el('option', {value:''}, '(بدون فضای کاری)'));
  STATE.workspaces.forEach(function(w){ ws.appendChild(el('option', {value:w.id}, w.name)); });
  var status = el('p', {class:'err muted'});
  var submitBtn = el('button', {class:'btn'}, 'ساخت پروژه');

  submitBtn.addEventListener('click', function(){
    var t = title.value.trim(), g = goal.value.trim();
    if (!t || !g){ status.textContent = 'عنوان و هدف را پر کن.'; return; }
    submitBtn.disabled = true; status.textContent = '';
    api('/projects', {method:'POST', json:{
      owner_id: STATE.userId, title: t, goal: g, mode: mode.value,
      workspace_id: ws.value ? Number(ws.value) : null,
      budget: budget.value ? Number(budget.value) : null
    }}).then(function(p){ onDone(); go('projects/' + p.id); })
      .catch(function(e){ status.textContent = e.message; submitBtn.disabled = false; });
  });

  return el('div', {class:'card stack', style:'margin-bottom:14px'},
    el('h3', {text:'پروژه‌ی جدید'}),
    el('label', {class:'f'}, 'عنوان', title),
    el('label', {class:'f'}, 'هدف', goal),
    el('div', {class:'grid grid-2'},
      el('label', {class:'f'}, 'حالت', mode),
      el('label', {class:'f'}, 'بودجه (دلار)', budget)),
    el('label', {class:'f'}, 'فضای کاری', ws),
    el('div', {class:'row'}, submitBtn, status)
  );
}

/* ============================== project detail ============================== */

function renderProjectDetail(root, projectId){
  root.appendChild(el('div', {class:'page-head'},
    el('button', {class:'btn ghost sm', onclick:function(){ go('projects'); }}, '← پروژه‌ها'),
    el('h2', {id:'proj-title', text:'…'})));
  var body = el('div', {class:'stack'});
  root.appendChild(body);
  body.appendChild(loadingBox());

  var project, tasks, events, usage;
  var tab = 'overview';

  function reload(){
    return Promise.all([
      api('/projects/' + projectId),
      api('/projects/' + projectId + '/tasks'),
      api('/projects/' + projectId + '/events'),
      api('/projects/' + projectId + '/usage'),
    ]).then(function(r){
      project = r[0]; tasks = r[1]; events = r[2]; usage = r[3];
      root.querySelector('#proj-title').textContent = project.title;
      draw();
    });
  }

  function draw(){
    clear(body);
    var tabs = el('div', {class:'tabs', role:'tablist'},
      tabBtn('overview', 'بررسی'), tabBtn('tasks', 'وظایف'), tabBtn('chat', 'گفتگو با مدیر'),
      tabBtn('memory', 'حافظه'), tabBtn('events', 'رویدادها'), tabBtn('usage', 'مصرف'));
    body.appendChild(tabs);
    var panel = el('div', {class:'stack'});
    body.appendChild(panel);
    if (tab === 'overview') drawOverview(panel);
    else if (tab === 'tasks') drawTasks(panel);
    else if (tab === 'chat') drawChat(panel);
    else if (tab === 'memory') drawMemory(panel);
    else if (tab === 'events') drawEvents(panel);
    else if (tab === 'usage') drawUsage(panel);
  }
  function tabBtn(key, label){
    var b = el('button', {class:'tab' + (tab === key ? ' active' : ''), role:'tab', text:label});
    b.addEventListener('click', function(){ tab = key; draw(); });
    return b;
  }

  function drawOverview(panel){
    panel.appendChild(el('div', {class:'card stack'},
      el('div', {class:'row'}, el('span', {class:'chip', text: STAGE_FA[project.stage] || project.stage}),
        el('span', {class:'chip', text: MODE_FA[project.mode] || project.mode}),
        project.paused ? el('span', {class:'chip', text:'متوقف‌شده'}) : null),
      el('p', {text: project.goal}),
      el('div', {class:'btn-row'},
        stageButton(),
        el('button', {class:'btn ghost sm', onclick: onTogglePause}, project.paused ? 'ازسرگیری' : 'توقف')
      )
    ));

    var pendingPlanTasks = tasks.filter(function(t){ return t.status === 'CREATED'; });
    var lastPlanEvent = null;
    events.forEach(function(e){ if (e.type === 'plan.proposed') lastPlanEvent = e; });
    var afterPlanRequested = lastPlanEvent ? events.filter(function(e){ return e.id > lastPlanEvent.id && e.type === 'specialist.requested'; }) : [];

    if (pendingPlanTasks.length && lastPlanEvent){
      panel.appendChild(planReviewCard(lastPlanEvent, pendingPlanTasks, afterPlanRequested));
    } else {
      var planStatus = el('p', {class:'err muted'});
      var planBtn = el('button', {class:'btn', text: tasks.length ? 'برنامه‌ریزی دوباره' : 'برنامه بریز'});
      planBtn.addEventListener('click', function(){
        planBtn.disabled = true; planStatus.textContent = '';
        api('/projects/' + projectId + '/plan', {method:'POST'})
          .then(function(){ return reload(); })
          .catch(function(e){ planStatus.textContent = e.message; planBtn.disabled = false; });
      });
      panel.appendChild(el('div', {class:'card stack'}, el('h3', {text:'برنامه‌ریزی'}),
        el('p', {class:'muted', text:'مدیر هدف پروژه را به یک نقشه‌ی راه از وظایف تبدیل می‌کند.'}),
        el('div', {class:'btn-row'}, planBtn), planStatus));
    }
  }

  function stageButton(){
    var idx = STAGES.indexOf(project.stage);
    var next = (project.stage === 'ITERATION') ? 'DISCOVERY' : (idx >= 0 && idx + 1 < STAGES.length ? STAGES[idx + 1] : null);
    if (!next) return null;
    var b = el('button', {class:'btn sm', text:'پیشروی به «' + STAGE_FA[next] + '»'});
    b.addEventListener('click', function(){
      b.disabled = true;
      api('/projects/' + projectId + '/stage', {method:'POST', json:{stage: next}})
        .then(reload).catch(function(e){ alert(e.message); b.disabled = false; });
    });
    return b;
  }
  function onTogglePause(){
    api('/projects/' + projectId + '/pause', {method:'POST', json:{paused: !project.paused}}).then(reload).catch(function(e){ alert(e.message); });
  }

  function planReviewCard(planEvent, pendingTasks, requestedSpecs){
    var card = el('div', {class:'card stack'}, el('h3', {text:'برنامه در انتظار تأیید'}),
      el('p', {text: planEvent.payload.understanding || ''}));
    if ((planEvent.payload.assumptions || []).length){
      var a = el('div', {}, el('p', {class:'muted', text:'فرض‌ها:'}));
      planEvent.payload.assumptions.forEach(function(x){ a.appendChild(el('p', {class:'muted', text:'• ' + x})); });
      card.appendChild(a);
    }
    var ol = el('ol', {class:'list', style:'padding-inline-start:0'});
    pendingTasks.forEach(function(t){
      ol.appendChild(el('div', {class:'list-item', style:'cursor:default'},
        el('div', {class:'top'}, el('b', {text:t.title}), el('span', {class:'chip', text:t.owner || '?'})),
        riskChip(t.risk)
      ));
    });
    card.appendChild(ol);
    if (requestedSpecs.length){
      var rs = el('div', {}, el('p', {class:'muted', text:'متخصص‌های ثبت‌نشده که درخواست شدند:'}));
      requestedSpecs.forEach(function(e){ rs.appendChild(el('p', {class:'muted', text:'• ' + e.payload.title + ' (' + e.payload.agent + ')'})); });
      card.appendChild(rs);
    }
    var fb = el('textarea', {placeholder:'اگر برنامه را قبول نداری، بنویس چه چیزی عوض شود'});
    var status = el('p', {class:'err muted'});
    var approveBtn = el('button', {class:'btn', text:'تأیید برنامه'});
    var rejectBtn = el('button', {class:'btn ghost', text:'رد و بازنویسی'});
    approveBtn.addEventListener('click', function(){
      approveBtn.disabled = true;
      api('/projects/' + projectId + '/plan/approve', {method:'POST'}).then(reload)
        .catch(function(e){ status.textContent = e.message; approveBtn.disabled = false; });
    });
    rejectBtn.addEventListener('click', function(){
      var f = fb.value.trim();
      if (!f){ status.textContent = 'بنویس چه چیزی باید عوض شود.'; return; }
      rejectBtn.disabled = true;
      api('/projects/' + projectId + '/plan/reject', {method:'POST', json:{feedback:f}}).then(reload)
        .catch(function(e){ status.textContent = e.message; rejectBtn.disabled = false; });
    });
    card.appendChild(el('label', {class:'f'}, 'بازخورد برای رد برنامه (اختیاری تا وقتی رد نکرده‌ای)', fb));
    card.appendChild(el('div', {class:'btn-row'}, approveBtn, rejectBtn));
    card.appendChild(status);
    card.appendChild(feedbackWidget(projectId, 'manager', null));
    return card;
  }

  function drawChat(panel){
    var card = el('div', {class:'card'});
    var list = el('div', {class:'chat-list'});
    card.appendChild(list);
    panel.appendChild(card);
    var input = el('textarea', {placeholder:'به مدیر چه می‌خواهی بگویی؟'});
    var sendBtn = el('button', {class:'btn', text:'ارسال'});
    var status = el('p', {class:'err muted'});
    panel.appendChild(el('div', {class:'card stack'},
      el('div', {class:'chat-input-row'}, input, sendBtn), status));

    function suggestedActionButton(action){
      var m = /^checkpoint:(\d+)$/.exec(action);
      var b = el('button', {class:'btn sm'});
      if (action === 'plan'){
        b.textContent = tasks.length ? 'برنامه‌ریزی دوباره' : 'برنامه بریز';
        b.addEventListener('click', function(){
          b.disabled = true;
          api('/projects/' + projectId + '/plan', {method:'POST'})
            .then(function(){ tab = 'overview'; return reload(); })
            .catch(function(e){ alert(e.message); b.disabled = false; });
        });
      } else if (action === 'approve'){
        b.textContent = 'تأیید برنامه';
        b.addEventListener('click', function(){
          b.disabled = true;
          api('/projects/' + projectId + '/plan/approve', {method:'POST'})
            .then(function(){ tab = 'overview'; return reload(); })
            .catch(function(e){ alert(e.message); b.disabled = false; });
        });
      } else if (m){
        var tid = Number(m[1]);
        b.textContent = 'باز کردن نقطه‌ی تصمیم';
        b.addEventListener('click', function(){
          b.disabled = true;
          api('/projects/' + projectId + '/tasks/' + tid + '/checkpoint', {method:'POST'})
            .then(function(){ tab = 'tasks'; return reload(); })
            .catch(function(e){ alert(e.message); b.disabled = false; });
        });
      } else {
        return null;
      }
      return b;
    }

    function chatBubble(m){
      var wrap = el('div', {class:'stack', style:'gap:6px;align-items:' + (m.role === 'user' ? 'flex-end' : 'flex-start')});
      wrap.appendChild(el('div', {class:'chat-msg role-' + m.role}, m.text, el('span', {class:'chat-time', text:fmtTime(m.created_at)})));
      if (m.role === 'manager'){
        var actionBtn = m.suggested_action ? suggestedActionButton(m.suggested_action) : null;
        if (actionBtn) wrap.appendChild(actionBtn);
        wrap.appendChild(feedbackWidget(projectId, 'manager', null));
      }
      return wrap;
    }

    function loadMessages(){
      clear(list); list.appendChild(loadingBox());
      api('/projects/' + projectId + '/chat').then(function(msgs){
        clear(list);
        if (!msgs.length){ list.appendChild(el('p', {class:'muted', text:'هنوز گفتگویی نیست؛ چیزی از مدیر بپرس.'})); return; }
        msgs.forEach(function(m){ list.appendChild(chatBubble(m)); });
        list.scrollTop = list.scrollHeight;
      }).catch(function(e){ clear(list); list.appendChild(errorBox(e.message, loadMessages)); });
    }

    sendBtn.addEventListener('click', function(){
      var text = input.value.trim();
      if (!text) return;
      sendBtn.disabled = true; status.textContent = '';
      api('/projects/' + projectId + '/chat', {method:'POST', json:{text:text}})
        .then(function(){ input.value = ''; sendBtn.disabled = false; loadMessages(); })
        .catch(function(e){ status.textContent = e.message; sendBtn.disabled = false; });
    });
    loadMessages();
  }

  function drawTasks(panel){
    if (!tasks.length){ panel.appendChild(emptyBox('🗂️', 'هنوز وظیفه‌ای نیست', 'اول یک برنامه بریز.')); return; }
    var list = el('div', {class:'list'});
    tasks.forEach(function(t){ list.appendChild(taskRow(t)); });
    panel.appendChild(list);
  }

  function taskRow(t){
    var row = el('div', {class:'card stack'},
      el('div', {class:'top row', style:'justify-content:space-between'},
        el('b', {text:t.title}),
        el('div', {class:'row'}, statusChip(t.status), riskChip(t.risk))),
      el('p', {class:'muted', text:'عهده‌دار: ' + (t.owner || '—') + (t.depends_on.length ? ' · وابسته به: #' + t.depends_on.join('، #') : '')})
    );
    if (t.status === 'READY') row.appendChild(checkpointArea(t));
    if (t.status === 'RUNNING') row.appendChild(runArea(t));
    // Not shown while RUNNING: a redo loop (COMPLETED -> READY -> RUNNING) keeps the old output
    // until this run finishes, and showing it next to the "اجرا" button would look like a fresh result.
    if (t.output && t.status !== 'RUNNING') row.appendChild(outputBox(t.output));
    return row;
  }

  function runArea(t){
    var holder = el('div', {class:'stack'});
    var btn = el('button', {class:'btn sm', text:'اجرا'});
    holder.appendChild(btn);
    btn.addEventListener('click', function(){
      btn.disabled = true;
      clear(holder);
      holder.appendChild(loadingBox('در حال اجرای وظیفه…'));
      api('/projects/' + projectId + '/tasks/' + t.id + '/run', {method:'POST'})
        .then(reload)
        .catch(function(e){
          clear(holder);
          holder.appendChild(errorBox(e.message));
          holder.appendChild(el('button', {class:'btn ghost sm', text:'دوباره امتحان کن',
            onclick:function(){ clear(holder); holder.appendChild(btn); btn.disabled = false; }}));
        });
    });
    return holder;
  }

  var EVIDENCE_FA = {FACT:'واقعیت', INFERENCE:'استنتاج', HYPOTHESIS:'فرضیه'};
  function evidenceChip(type){
    return el('span', {class:'chip ev-' + String(type).toLowerCase(), text: EVIDENCE_FA[type] || type});
  }

  function outputBox(output){
    var box = el('div', {class:'stack', style:'margin-top:8px'});
    if (output.summary) box.appendChild(el('p', {}, el('b', {text:'خلاصه: '}), output.summary));
    if (output.findings && output.findings.length){
      var list = el('div', {class:'stack', style:'gap:6px'});
      output.findings.forEach(function(f){
        list.appendChild(el('div', {class:'row', style:'align-items:flex-start;gap:8px'},
          evidenceChip(f.type),
          el('span', {}, el('b', {text:f.claim}), el('small', {class:'muted', text:' — ' + f.basis}))));
      });
      box.appendChild(list);
    }
    if (output.lesson) box.appendChild(el('p', {class:'muted'}, el('b', {text:'درس: '}), output.lesson));
    if (output.next) box.appendChild(el('p', {class:'muted'}, el('b', {text:'گام بعد: '}), output.next));
    return box;
  }

  function checkpointArea(t){
    var holder = el('div', {class:'stack'});
    var btn = el('button', {class:'btn sm', text:'نقطه‌ی تصمیم'});
    holder.appendChild(btn);
    btn.addEventListener('click', function(){
      btn.disabled = true;
      clear(holder);
      holder.appendChild(loadingBox('مدیر دارد گزینه‌ها را آماده می‌کند…'));
      api('/projects/' + projectId + '/tasks/' + t.id + '/checkpoint', {method:'POST'}).then(function(r){
        clear(holder);
        if (r.auto_decided){
          holder.appendChild(el('p', {class:'muted', text:'به‌صورت خودکار تصمیم گرفته شد؛ وظیفه در حال اجراست.'}));
          reload();
          return;
        }
        holder.appendChild(checkpointUI(t, r.checkpoint));
      }).catch(function(e){
        clear(holder);
        holder.appendChild(errorBox(e.message));
        holder.appendChild(el('button', {class:'btn ghost sm', text:'دوباره امتحان کن', onclick:function(){ clear(holder); holder.appendChild(btn); btn.disabled = false; }}));
      });
    });
    return holder;
  }

  function checkpointUI(t, cp){
    var picked = cp.recommended;
    var box = el('div', {class:'stack'});
    box.appendChild(el('p', {}, el('b', {text:'چالش: '}), cp.challenge));
    var group = el('div', {class:'stack', style:'gap:8px', role:'radiogroup'});
    cp.options.forEach(function(o, i){
      var opt = el('button', {class:'opt', role:'radio', 'aria-checked': String(i === picked)},
        el('span', {},
          el('b', {}, o.title, i === cp.recommended ? el('span', {class:'rec', text:'پیشنهاد مدیر'}) : null),
          el('small', {text:o.tradeoff})));
      opt.addEventListener('click', function(){
        picked = i;
        group.querySelectorAll('.opt').forEach(function(x, xi){ x.setAttribute('aria-checked', String(xi === i)); });
      });
      group.appendChild(opt);
    });
    box.appendChild(group);
    box.appendChild(el('p', {class:'muted'}, el('b', {text:'چرا: '}), cp.why));
    var note = el('textarea', {placeholder:'یادداشت یا نکته (اختیاری)'});
    box.appendChild(el('label', {class:'f'}, 'یادداشت', note));
    var status = el('p', {class:'err muted'});
    var decideBtn = el('button', {class:'btn', text:'تأیید و اجرا'});
    decideBtn.addEventListener('click', function(){
      decideBtn.disabled = true;
      api('/projects/' + projectId + '/tasks/' + t.id + '/decide', {method:'POST', json:{option:picked, note: note.value.trim() || null}})
        .then(reload).catch(function(e){ status.textContent = e.message; decideBtn.disabled = false; });
    });
    box.appendChild(el('div', {class:'btn-row'}, decideBtn));
    box.appendChild(status);
    box.appendChild(feedbackWidget(projectId, 'manager', t.id));
    return box;
  }

  function drawEvents(panel){
    if (!events.length){ panel.appendChild(emptyBox('📜', 'رویدادی ثبت نشده')); return; }
    var card = el('div', {class:'card'});
    events.slice().reverse().forEach(function(e){ card.appendChild(eventRow(e)); });
    panel.appendChild(card);
  }

  function drawUsage(panel){
    panel.appendChild(el('div', {class:'grid grid-3'},
      el('div', {class:'card stat'}, el('b', {text: fmtNum(usage.calls)}), el('span', {text:'تعداد فراخوانی مدل'})),
      el('div', {class:'card stat'}, el('b', {text: fmtMoney(usage.cost_usd)}), el('span', {text:'هزینه‌ی مصرف‌شده'})),
      el('div', {class:'card stat'}, el('b', {text: fmtMoney(usage.budget_usd)}), el('span', {text:'سقف بودجه'}))
    ));
    panel.appendChild(el('div', {class:'grid grid-2'},
      el('div', {class:'card stat'}, el('b', {text: fmtNum(usage.input_tokens)}), el('span', {text:'توکن ورودی'})),
      el('div', {class:'card stat'}, el('b', {text: fmtNum(usage.output_tokens)}), el('span', {text:'توکن خروجی'}))
    ));
  }

  function drawMemory(panel){
    var formCard = el('div', {class:'card stack'});
    var listBox = el('div', {class:'stack'});
    var catSel = el('select', {});
    Object.keys(MEMORY_CATEGORY_FA).forEach(function(c){ catSel.appendChild(el('option', {value:c, text:MEMORY_CATEGORY_FA[c]})); });
    var titleInput = el('input', {type:'text', placeholder:'عنوان'});
    var contentInput = el('textarea', {placeholder:'محتوا'});
    var addStatus = el('p', {class:'err muted'});
    var addBtn = el('button', {class:'btn sm', text:'افزودن'});

    function load(){
      clear(listBox); listBox.appendChild(loadingBox());
      Promise.all([api('/projects/' + projectId + '/memory'), api('/projects/' + projectId + '/learning')])
        .then(function(r){
          clear(listBox);
          var items = r[0], traces = r[1];
          if (!items.length && !traces.length){
            listBox.appendChild(emptyBox('🧠', 'حافظه‌ای ثبت نشده',
              'وقتی وظیفه‌ای اجرا شود، یافته‌ها و درس‌ها اینجا نشان داده می‌شوند.'));
            return;
          }
          // Merge both kinds into one chronological feed — each card shows a timestamp, so the
          // list should read newest-first regardless of which endpoint it came from.
          var entries = items.map(function(m){ return {kind:'memory', at:m.created_at, data:m}; })
            .concat(traces.map(function(t){ return {kind:'learning', at:t.created_at, data:t}; }));
          entries.sort(function(a, b){ return a.at < b.at ? 1 : a.at > b.at ? -1 : 0; });
          entries.forEach(function(entry){
            if (entry.kind === 'learning'){
              var t = entry.data;
              listBox.appendChild(el('div', {class:'card stack'},
                el('div', {class:'row', style:'justify-content:space-between'},
                  el('span', {class:'chip', text:'درس'}), el('small', {class:'muted', text: fmtTime(t.created_at)})),
                el('b', {text:t.concept}), el('p', {class:'muted', text:t.explanation})));
            } else {
              var m = entry.data;
              listBox.appendChild(el('div', {class:'card stack'},
                el('div', {class:'row', style:'justify-content:space-between'},
                  el('span', {class:'chip', text: MEMORY_CATEGORY_FA[m.category] || m.category}),
                  el('small', {class:'muted', text: fmtTime(m.created_at)})),
                el('b', {text:m.title}), el('p', {class:'muted', text:m.content})));
            }
          });
        }).catch(function(e){ clear(listBox); listBox.appendChild(errorBox(e.message, load)); });
    }

    addBtn.addEventListener('click', function(){
      if (!titleInput.value.trim() || !contentInput.value.trim()){
        addStatus.textContent = 'عنوان و محتوا را پر کن.'; return;
      }
      addBtn.disabled = true; addStatus.textContent = '';
      api('/projects/' + projectId + '/memory', {method:'POST', json:{
        category: catSel.value, title: titleInput.value.trim(), content: contentInput.value.trim(),
      }}).then(function(){
        titleInput.value = ''; contentInput.value = ''; addBtn.disabled = false;
        load();
      }).catch(function(e){ addStatus.textContent = e.message; addBtn.disabled = false; });
    });

    formCard.appendChild(el('h3', {text:'افزودن یادداشت به حافظه'}));
    formCard.appendChild(el('div', {class:'grid grid-2'},
      el('label', {class:'f'}, 'دسته', catSel), el('label', {class:'f'}, 'عنوان', titleInput)));
    formCard.appendChild(el('label', {class:'f'}, 'محتوا', contentInput));
    formCard.appendChild(el('div', {class:'btn-row'}, addBtn));
    formCard.appendChild(addStatus);
    panel.appendChild(formCard);
    panel.appendChild(listBox);
    load();
  }

  reload().catch(function(e){ clear(body); body.appendChild(errorBox(e.message, function(){ route(); })); });
}

/* ============================== agents ============================== */

function renderAgentsList(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:'ایجنت‌ها'}),
    el('button', {class:'btn sm', onclick:function(){ go('agents/new'); }}, '+ ساخت Agent جدید')));
  var body = el('div', {class:'list'});
  root.appendChild(body);
  body.appendChild(loadingBox());
  api('/agents').then(function(agents){
    clear(body);
    if (!agents.length){ body.appendChild(emptyBox('🤖', 'هنوز ایجنتی ثبت نشده')); return; }
    agents.forEach(function(a){
      body.appendChild(el('button', {class:'list-item', onclick:function(){ go('agents/' + encodeURIComponent(a.name)); }},
        el('div', {class:'top'}, el('b', {text:a.name}),
          el('div', {class:'row'}, el('span', {class:'chip', text: ROLE_FA[a.model_role] || a.model_role}),
            a.active ? el('span', {class:'chip', text:'فعال'}) : el('span', {class:'chip', text:'غیرفعال'}))),
        el('span', {class:'muted', text:a.description}),
        el('div', {class:'row'}, a.capabilities.map(function(c){ return el('span', {class:'chip', text:c}); }))
      ));
    });
  }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message)); });
}

function renderAgentDetail(root, name){
  root.appendChild(el('div', {class:'page-head'},
    el('button', {class:'btn ghost sm', onclick:function(){ go('agents'); }}, '← ایجنت‌ها'),
    el('h2', {text:name})));
  var tabsHolder = el('div', {class:'tabs', role:'tablist'});
  var body = el('div', {class:'stack'});
  root.appendChild(tabsHolder);
  root.appendChild(body);
  body.appendChild(loadingBox());

  var tab = 'overview', agentData = null, skillsData = null, feedbackCount = null;

  function tabBtn(key, label){
    var b = el('button', {class:'tab' + (tab === key ? ' active' : ''), role:'tab', text:label});
    b.addEventListener('click', function(){ tab = key; drawTabs(); draw(); });
    return b;
  }
  function drawTabs(){
    clear(tabsHolder);
    tabsHolder.appendChild(tabBtn('overview', 'بررسی'));
    tabsHolder.appendChild(tabBtn('feedback', 'آموزش' + (feedbackCount !== null ? ' (' + fmtNum(feedbackCount) + ')' : '')));
  }
  function draw(){
    clear(body);
    if (tab === 'overview') drawOverview(body);
    else drawFeedback(body);
  }

  function drawFeedback(panel){
    panel.appendChild(loadingBox());
    api('/agents/' + encodeURIComponent(name) + '/feedback').then(function(list){
      feedbackCount = list.length;
      drawTabs();
      clear(panel);
      if (!list.length){ panel.appendChild(emptyBox('🎓', 'هنوز بازخوردی ثبت نشده', 'زیر پاسخ‌های این ایجنت می‌توانی امتیاز بدهی.')); return; }
      var card = el('div', {class:'card'});
      list.forEach(function(f){
        card.appendChild(el('div', {class:'stack', style:'gap:4px;padding-block:8px;border-top:1px solid var(--line)'},
          el('div', {class:'row', style:'justify-content:space-between'},
            el('span', {class:'chip', text: f.rating === 'up' ? '👍 مثبت' : '👎 منفی'}),
            el('time', {class:'muted', text:fmtTime(f.created_at)})),
          f.note ? el('p', {text:f.note}) : null,
          f.task_id ? el('span', {class:'muted', text:'وظیفه‌ی #' + f.task_id}) : null));
      });
      panel.appendChild(card);
    }).catch(function(e){ clear(panel); panel.appendChild(errorBox(e.message, function(){ drawFeedback(panel); })); });
  }

  function drawOverview(panel){
    var agent = agentData, allSkills = skillsData, body = panel;
    body.appendChild(el('div', {class:'card stack'},
      el('p', {text: agent.description}),
      el('div', {class:'row'},
        el('span', {class:'chip', text:'نقش مدل: ' + (ROLE_FA[agent.model_role] || agent.model_role)}),
        agent.active ? el('span', {class:'chip', text:'فعال'}) : el('span', {class:'chip', text:'غیرفعال'}))
    ));

    var caps = el('div', {class:'card stack'}, el('h3', {text:'قابلیت‌ها'}),
      el('div', {class:'row'}, agent.capabilities.map(function(c){ return el('span', {class:'chip', text:c}); })));
    body.appendChild(caps);

    var tools = el('div', {class:'card stack'}, el('h3', {text:'ابزارها'}),
      agent.tools.length ? el('div', {class:'row'}, agent.tools.map(function(t){ return el('span', {class:'chip', text:t}); }))
        : el('p', {class:'muted', text:'ابزاری تخصیص داده نشده.'}));
    body.appendChild(tools);

    var perms = el('div', {class:'card stack'}, el('h3', {text:'مجوزها'}));
    var permKeys = Object.keys(agent.permissions);
    if (!permKeys.length) perms.appendChild(el('p', {class:'muted', text:'مجوزی تعریف نشده.'}));
    else permKeys.forEach(function(k){
      perms.appendChild(el('div', {class:'row', style:'justify-content:space-between'},
        el('span', {text:k}), el('span', {class:'chip', text: agent.permissions[k] ? 'مجاز' : 'غیرمجاز'})));
    });
    body.appendChild(perms);

    var skillsCard = el('div', {class:'card stack'}, el('h3', {text:'مهارت‌ها'}));
    if (!agent.skills.length) skillsCard.appendChild(el('p', {class:'muted', text:'مهارتی تخصیص داده نشده.'}));
    agent.skills.forEach(function(sname){
      var meta = allSkills.filter(function(s){ return s.name === sname; })[0];
      var wrap = el('div', {class:'card', style:'background:var(--surface-2)'});
      var toggleBtn = el('button', {class:'btn ghost sm'}, meta ? meta.summary : sname);
      var bodyHolder = el('div');
      toggleBtn.addEventListener('click', function(){
        if (bodyHolder.firstChild){ clear(bodyHolder); return; }
        bodyHolder.appendChild(loadingBox());
        api('/skills/' + encodeURIComponent(sname)).then(function(full){
          clear(bodyHolder);
          bodyHolder.appendChild(el('p', {class:'muted', style:'white-space:pre-wrap', text: full.body}));
        }).catch(function(e){ clear(bodyHolder); bodyHolder.appendChild(errorBox(e.message)); });
      });
      wrap.appendChild(toggleBtn); wrap.appendChild(bodyHolder);
      skillsCard.appendChild(wrap);
    });
    body.appendChild(skillsCard);
  }

  drawTabs();
  Promise.all([api('/agents/' + encodeURIComponent(name)), api('/skills')]).then(function(r){
    agentData = r[0]; skillsData = r[1];
    draw();
  }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message)); });
}

/* ============================== agent intake (guided blueprint interview) ============================== */

function renderAgentIntake(root){
  root.appendChild(el('div', {class:'page-head'},
    el('button', {class:'btn ghost sm', onclick:function(){ go('agents'); }}, '← ایجنت‌ها'),
    el('h2', {text:'ساخت Agent جدید'})));
  var body = el('div', {class:'stack'});
  root.appendChild(body);
  var answers = {};

  function start(){
    clear(body);
    body.appendChild(loadingBox());
    api('/interview').then(draw).catch(function(e){ clear(body); body.appendChild(errorBox(e.message, start)); });
  }

  function draw(state){
    clear(body);
    if (state.done){ drawSummary(state); return; }
    drawQuestion(state.question);
  }

  function submit(target, value){
    clear(body);
    body.appendChild(loadingBox());
    api('/interview/answer', {method:'POST', json:{answers: answers, target: target, value: value}})
      .then(function(state){ answers[target] = value; draw(state); })
      .catch(function(e){ clear(body); body.appendChild(errorBox(e.message, function(){ submit(target, value); })); });
  }

  function drawQuestion(q){
    var card = el('div', {class:'card stack'}, el('h3', {text:q.text}));
    if (q.type === 'single_choice' || q.type === 'confirm'){
      var opts = q.type === 'confirm' ? [{value:true,label:'بله'},{value:false,label:'خیر'}] : q.options;
      card.appendChild(el('div', {class:'row'}, opts.map(function(o){
        var label = o.label + (o.recommended ? ' ✓' : '');
        return el('button', {class:'btn' + (o.recommended ? '' : ' ghost') + ' sm', onclick:function(){ submit(q.target, o.value); }}, label);
      })));
    } else if (q.type === 'multi_choice'){
      var chosen = [];
      var chips = q.options.map(function(o){
        var b = el('button', {class:'tab', text: o.label + (o.recommended ? ' ✓' : '')});
        b.addEventListener('click', function(){
          var i = chosen.indexOf(o.value);
          if (i === -1){ chosen.push(o.value); b.classList.add('active'); } else { chosen.splice(i, 1); b.classList.remove('active'); }
        });
        return b;
      });
      card.appendChild(el('div', {class:'row'}, chips));
      card.appendChild(el('button', {class:'btn sm', style:'margin-top:8px', onclick:function(){ submit(q.target, chosen); }}, 'ادامه'));
    } else {
      var input = el('input', {type:'text'});
      card.appendChild(input);
      card.appendChild(el('button', {class:'btn sm', style:'margin-top:8px', onclick:function(){ submit(q.target, input.value); }}, 'ادامه'));
    }
    if (q.note) card.appendChild(el('p', {class:'muted', text:q.note}));
    body.appendChild(card);
  }

  function drawSummary(state){
    var bp = state.blueprint;
    var card = el('div', {class:'card stack'}, el('h3', {text:'خلاصه‌ی Blueprint'}));
    Object.keys(bp).forEach(function(section){
      card.appendChild(el('div', {class:'row', style:'justify-content:space-between'},
        el('b', {text:section}), el('span', {class:'muted', text: JSON.stringify(bp[section])})));
    });
    if (state.issues && state.issues.length){
      card.appendChild(el('h3', {text:'موارد ناقص'}));
      state.issues.forEach(function(i){ card.appendChild(el('p', {class:'muted', text:'⚠️ ' + i})); });
    } else {
      card.appendChild(el('p', {text:'✅ Blueprint معتبر است.'}));
    }
    card.appendChild(el('p', {text:'این مسیر را می‌رویم؟'}));
    body.appendChild(card);
  }

  start();
}

/* ============================== settings ============================== */

function getPath(obj, path){
  var parts = path.split('.'); var cur = obj;
  for (var i = 0; i < parts.length; i++){
    if (cur === undefined || cur === null) return undefined;
    cur = cur[parts[i]];
  }
  return cur;
}
function setPath(obj, path, value){
  var parts = path.split('.'); var cur = obj;
  for (var i = 0; i < parts.length - 1; i++){
    if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) cur[parts[i]] = {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
}

function renderSettings(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:'تنظیمات'})));
  var scopePicker = el('div', {class:'tabs', role:'tablist'});
  var scopeExtra = el('div', {class:'card', id:'scope-extra'});
  var body = el('div', {class:'stack'});
  root.appendChild(scopePicker);
  root.appendChild(scopeExtra);
  root.appendChild(body);

  var scope = 'global';
  var scopeId = 0;
  var scopeLabels = {global:'سراسری', workspace:'فضای کاری', project:'پروژه', task:'وظیفه'};

  Object.keys(scopeLabels).forEach(function(s){
    var b = el('button', {class:'tab' + (s === scope ? ' active' : ''), text: scopeLabels[s]});
    b.addEventListener('click', function(){
      scope = s; scopeId = 0;
      scopePicker.querySelectorAll('.tab').forEach(function(x){ x.classList.remove('active'); });
      b.classList.add('active');
      drawScopeExtra();
    });
    scopePicker.appendChild(b);
  });

  function drawScopeExtra(){
    clear(scopeExtra);
    clear(body);
    if (scope === 'global'){ scopeId = 0; loadLayer(); return; }
    if (scope === 'workspace'){
      var loading = loadingBox();
      scopeExtra.appendChild(loading);
      fetchWorkspaces().then(function(ws){
        clear(scopeExtra);
        if (!ws.length){ scopeExtra.appendChild(el('p', {class:'muted', text:'هیچ فضای کاری‌ای نیست.'})); body.appendChild(el('p')); return; }
        var sel = el('select', {}); ws.forEach(function(w){ sel.appendChild(el('option', {value:w.id}, w.name)); });
        sel.addEventListener('change', function(){ scopeId = Number(sel.value); loadLayer(); });
        scopeExtra.appendChild(el('label', {class:'f'}, 'فضای کاری', sel));
        scopeId = Number(sel.value); loadLayer();
      });
      return;
    }
    if (scope === 'project'){
      var loading2 = loadingBox();
      scopeExtra.appendChild(loading2);
      fetchProjects().then(function(ps){
        clear(scopeExtra);
        if (!ps.length){ scopeExtra.appendChild(el('p', {class:'muted', text:'هیچ پروژه‌ای نیست.'})); return; }
        var sel = el('select', {}); ps.forEach(function(p){ sel.appendChild(el('option', {value:p.id}, p.title)); });
        sel.addEventListener('change', function(){ scopeId = Number(sel.value); loadLayer(); });
        scopeExtra.appendChild(el('label', {class:'f'}, 'پروژه', sel));
        scopeId = Number(sel.value); loadLayer();
      });
      return;
    }
    if (scope === 'task'){
      var loading3 = loadingBox();
      scopeExtra.appendChild(loading3);
      fetchProjects().then(function(ps){
        clear(scopeExtra);
        if (!ps.length){ scopeExtra.appendChild(el('p', {class:'muted', text:'هیچ پروژه‌ای نیست.'})); return; }
        var psel = el('select', {}); ps.forEach(function(p){ psel.appendChild(el('option', {value:p.id}, p.title)); });
        var tsel = el('select', {});
        scopeExtra.appendChild(el('label', {class:'f'}, 'پروژه', psel));
        scopeExtra.appendChild(el('label', {class:'f'}, 'وظیفه', tsel));
        function loadTasks(){
          clear(tsel); clear(body);
          api('/projects/' + psel.value + '/tasks').then(function(ts){
            if (!ts.length){
              tsel.appendChild(el('option', {value:''}, '(وظیفه‌ای نیست)'));
              body.appendChild(el('p', {class:'muted', text:'این پروژه هنوز وظیفه‌ای ندارد.'}));
              return;
            }
            ts.forEach(function(t){ tsel.appendChild(el('option', {value:t.id}, t.title)); });
            scopeId = Number(tsel.value); loadLayer();
          });
        }
        psel.addEventListener('change', loadTasks);
        tsel.addEventListener('change', function(){ scopeId = Number(tsel.value); loadLayer(); });
        loadTasks();
      });
      return;
    }
  }

  function resolvedQuery(){
    if (scope === 'workspace') return 'workspace_id=' + scopeId;
    if (scope === 'project') return 'project_id=' + scopeId;
    if (scope === 'task') return 'task_id=' + scopeId;
    return '';
  }

  function loadLayer(){
    clear(body); body.appendChild(loadingBox());
    Promise.all([
      api('/settings/' + scope + '/' + scopeId),
      api('/settings/resolved' + (resolvedQuery() ? '?' + resolvedQuery() : ''))
    ]).then(function(r){ drawForm(r[0], r[1]); })
      .catch(function(e){ clear(body); body.appendChild(errorBox(e.message, loadLayer)); });
  }

  function drawForm(layer, resolved){
    clear(body);
    var overrides = JSON.parse(JSON.stringify(layer.values || {}));
    var status = el('p', {class:'err muted'});

    function numberField(path, label, step){
      var cur = getPath(overrides, path);
      var input = el('input', {type:'number', step: step || 'any', placeholder: 'به ارث می‌رسد: ' + getPath(resolved, path)});
      if (cur !== undefined) input.value = cur;
      input.addEventListener('input', function(){
        if (input.value === '') { unsetPath(overrides, path); }
        else setPath(overrides, path, Number(input.value));
      });
      return el('label', {class:'f'}, label, input);
    }
    function faLabel(faMap, v){
      if (v === null || v === undefined || v === '') return '—';
      return faMap[v] || v;
    }
    function selectField(path, label, options, faMap){
      var cur = getPath(overrides, path);
      var sel = el('select', {});
      sel.appendChild(el('option', {value:''}, 'به ارث می‌رسد (' + faLabel(faMap, getPath(resolved, path)) + ')'));
      options.forEach(function(o){ sel.appendChild(el('option', {value:o}, faMap[o] || o)); });
      sel.value = cur !== undefined ? cur : '';
      sel.addEventListener('change', function(){
        if (sel.value === '') unsetPath(overrides, path);
        else setPath(overrides, path, sel.value);
      });
      return el('label', {class:'f'}, label, sel);
    }
    function textField(path, label){
      var cur = getPath(overrides, path);
      var input = el('input', {type:'text', placeholder:'به ارث می‌رسد: ' + (getPath(resolved, path) || '')});
      if (cur !== undefined) input.value = cur;
      input.addEventListener('input', function(){
        if (input.value.trim() === '') unsetPath(overrides, path);
        else setPath(overrides, path, input.value.trim());
      });
      return el('label', {class:'f'}, label, input);
    }

    var general = el('fieldset', {}, el('legend', {text:'عمومی'}),
      selectField('mode', 'حالت', ['automatic','manual_learning'], MODE_FA),
      selectField('depth', 'عمق', ['quick','standard','deep'], DEPTH_FA),
      numberField('max_steps', 'حداکثر تعداد قدم', '1'));

    var models = el('fieldset', {}, el('legend', {text:'مدل‌ها'}));
    MODEL_ROLES.forEach(function(role){
      models.appendChild(el('div', {class:'card', style:'background:var(--surface-2)'},
        el('p', {class:'muted', text: ROLE_FA[role]}),
        el('div', {class:'grid grid-3'},
          selectField('models.' + role + '.provider', 'ارائه‌دهنده', PROVIDERS, {}),
          textField('models.' + role + '.model', 'مدل'),
          selectField('models.' + role + '.effort', 'سطح تلاش', ['low','medium','high'], {low:'کم', medium:'متوسط', high:'زیاد'}))));
    });
    if (resolved.model_access !== undefined){
      models.appendChild(el('div', {class:'card', style:'background:var(--surface-2)'},
        selectField('model_access', 'روش دسترسی به مدل', ['api_key','claude_account','ollama'], MODEL_ACCESS_FA)));
    }

    var budget = el('fieldset', {}, el('legend', {text:'بودجه و محدودیت‌ها'}),
      numberField('budget.project_usd', 'بودجه‌ی پروژه (دلار)', '0.01'),
      numberField('budget.task_usd', 'بودجه‌ی هر وظیفه (دلار)', '0.01'),
      numberField('budget.max_output_tokens', 'حداکثر توکن خروجی', '1'),
      numberField('budget.max_web_searches', 'حداکثر جستجوی وب در هر فراخوانی', '1'),
      numberField('context.max_chars', 'حداکثر کاراکتر زمینه', '1'));

    var approvals = el('fieldset', {}, el('legend', {text:'تأییدهای انسانی'}),
      selectField('approvals.low', 'ریسک کم', ['auto','manager','user'], APPROVAL_FA),
      selectField('approvals.medium', 'ریسک متوسط', ['auto','manager','user'], APPROVAL_FA),
      selectField('approvals.high', 'ریسک زیاد', ['auto','manager','user'], APPROVAL_FA));

    var saveBtn = el('button', {class:'btn', text:'ذخیره'});
    saveBtn.addEventListener('click', function(){
      saveBtn.disabled = true; status.textContent = '';
      api('/settings/' + scope + '/' + scopeId, {method:'PUT', json: overrides})
        .then(function(){ status.className = 'muted'; status.textContent = 'ذخیره شد.'; loadLayer(); })
        .catch(function(e){ status.className = 'err muted'; status.textContent = e.message; saveBtn.disabled = false; });
    });

    body.appendChild(el('div', {class:'card stack'}, general, models, budget, approvals,
      el('div', {class:'btn-row'}, saveBtn), status));
  }

  function unsetPath(obj, path){
    var parts = path.split('.'); var cur = obj;
    for (var i = 0; i < parts.length - 1; i++){
      if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) return;
      cur = cur[parts[i]];
    }
    delete cur[parts[parts.length - 1]];
  }

  drawScopeExtra();
}

/* ============================== observability ============================== */

function renderObservability(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:'مشاهده‌پذیری'})));
  var picker = el('div', {class:'card'});
  var body = el('div', {class:'stack'});
  root.appendChild(picker);
  root.appendChild(body);
  picker.appendChild(loadingBox());

  fetchProjects().then(function(ps){
    clear(picker);
    if (!ps.length){ picker.appendChild(el('p', {class:'muted', text:'هیچ پروژه‌ای نیست.'})); return; }
    var sel = el('select', {}); ps.forEach(function(p){ sel.appendChild(el('option', {value:p.id}, p.title)); });
    picker.appendChild(el('label', {class:'f'}, 'پروژه', sel));
    sel.addEventListener('change', function(){ load(Number(sel.value)); });
    load(Number(sel.value));
  }).catch(function(e){ clear(picker); picker.appendChild(errorBox(e.message)); });

  function load(projectId){
    clear(body); body.appendChild(loadingBox());
    Promise.all([
      api('/projects/' + projectId + '/events'),
      api('/projects/' + projectId + '/model_calls'),
    ]).then(function(r){ draw(r[0], r[1]); })
      .catch(function(e){ clear(body); body.appendChild(errorBox(e.message, function(){ load(projectId); })); });
  }

  function draw(events, calls){
    clear(body);
    var evCard = el('div', {class:'card stack'}, el('h3', {text:'رویدادها'}));
    if (!events.length) evCard.appendChild(el('p', {class:'muted', text:'رویدادی ثبت نشده.'}));
    else events.slice().reverse().forEach(function(e){ evCard.appendChild(eventRow(e)); });
    body.appendChild(evCard);

    var callCard = el('div', {class:'card stack'}, el('h3', {text:'فراخوانی‌های مدل'}));
    if (!calls.length){ callCard.appendChild(el('p', {class:'muted', text:'فراخوانی‌ای ثبت نشده.'})); body.appendChild(callCard); return; }
    var wrap = el('div', {class:'table-wrap'});
    var table = el('table', {},
      el('thead', {}, el('tr', {}, ['نقش','ارائه‌دهنده','مدل','ورودی','خروجی','جستجوی وب','هزینه','وضعیت','زمان'].map(function(h){ return el('th', {text:h}); }))),
      el('tbody', {}, calls.map(function(c){
        return el('tr', {},
          el('td', {text: c.role}), el('td', {text: c.provider}), el('td', {text: c.model}),
          el('td', {text: fmtNum(c.input_tokens)}), el('td', {text: fmtNum(c.output_tokens)}),
          el('td', {text: fmtNum(c.web_searches)}),
          el('td', {text: fmtMoney(c.cost_usd)}),
          el('td', {}, c.ok ? el('span', {class:'chip', text:'موفق'}) : el('span', {class:'chip risk-high', text:'ناموفق'})),
          el('td', {text: fmtTime(c.created_at)}));
      })));
    wrap.appendChild(table);
    callCard.appendChild(wrap);
    body.appendChild(callCard);
  }
}

/* ============================== benchmark ============================== */

var BENCH_EFFORTS = ['', 'low', 'medium', 'high'];
var BENCH_EFFORT_FA = {'':'—', low:'کم', medium:'متوسط', high:'زیاد'};

function renderBenchmark(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:'سنجش'})));
  var formCard = el('div', {class:'card stack'});
  var rows = el('div', {class:'stack', style:'gap:10px'});
  var status = el('p', {class:'err muted'});
  var addBtn = el('button', {class:'btn ghost sm', type:'button', text:'+ افزودن مسیر'});
  var runBtn = el('button', {class:'btn', text:'اجرا و مقایسه'});
  var body = el('div', {class:'stack'});

  function routeRow(defaults){
    defaults = defaults || {};
    var provider = el('select', {});
    PROVIDERS.forEach(function(p){ provider.appendChild(el('option', {value:p, text:p})); });
    provider.value = defaults.provider || 'anthropic';
    var model = el('input', {type:'text', placeholder:'claude-opus-5', value: defaults.model || ''});
    var effort = el('select', {});
    BENCH_EFFORTS.forEach(function(e){ effort.appendChild(el('option', {value:e, text:BENCH_EFFORT_FA[e]})); });
    effort.value = defaults.effort || '';
    var removeBtn = el('button', {class:'btn ghost sm', type:'button', text:'حذف'});
    var row = el('div', {class:'grid grid-3', style:'align-items:end;gap:8px'},
      el('label', {class:'f'}, 'ارائه‌دهنده', provider),
      el('label', {class:'f'}, 'مدل', model),
      el('div', {class:'row', style:'gap:8px;align-items:end'},
        el('label', {class:'f', style:'flex:1'}, 'تلاش', effort), removeBtn));
    row._route = function(){ return {provider: provider.value, model: model.value.trim(), effort: effort.value || null}; };
    removeBtn.addEventListener('click', function(){ if (rows.children.length > 1) rows.removeChild(row); });
    return row;
  }

  rows.appendChild(routeRow({provider:'anthropic', model:'claude-opus-5', effort:'low'}));
  rows.appendChild(routeRow({provider:'anthropic', model:'claude-sonnet-5', effort:'low'}));
  addBtn.addEventListener('click', function(){ if (rows.children.length < 5) rows.appendChild(routeRow({})); });

  runBtn.addEventListener('click', function(){
    var routes = Array.prototype.map.call(rows.children, function(r){ return r._route(); });
    if (routes.some(function(r){ return !r.model; })){ status.textContent = 'مدل هر مسیر را وارد کن.'; return; }
    runBtn.disabled = true; status.textContent = '';
    api('/benchmarks/run', {method:'POST', json:{routes: routes}})
      .then(function(){ runBtn.disabled = false; load(); })
      .catch(function(e){ status.textContent = e.message; runBtn.disabled = false; });
  });

  formCard.appendChild(el('p', {class:'muted',
    text:'همه‌ی مسیرها روی یک هدف ثابت اجرا می‌شوند تا مقایسه‌ی توکن/هزینه/زمان منصفانه بماند.'}));
  formCard.appendChild(rows);
  formCard.appendChild(el('div', {class:'btn-row'}, addBtn, runBtn));
  formCard.appendChild(status);
  root.appendChild(formCard);
  root.appendChild(body);

  function ratingCell(r){
    var sel = el('select', {});
    ['', '1', '2', '3', '4', '5'].forEach(function(v){ sel.appendChild(el('option', {value:v, text: v || '—'})); });
    sel.value = r.rating ? String(r.rating) : '';
    sel.addEventListener('change', function(){
      if (!sel.value) return;
      api('/benchmarks/' + r.id + '/rate', {method:'POST', json:{rating: Number(sel.value)}})
        .catch(function(e){ alert(e.message); });
    });
    return sel;
  }

  function benchmarkRow(r){
    return el('tr', {},
      el('td', {text:r.provider}), el('td', {text:r.model}), el('td', {text: BENCH_EFFORT_FA[r.effort || ''] || r.effort}),
      el('td', {text: fmtNum(r.input_tokens)}), el('td', {text: fmtNum(r.output_tokens)}),
      el('td', {text: fmtMoney(r.cost_usd)}), el('td', {text: fmtNum(r.duration_ms) + ' ms'}),
      el('td', {}, r.schema_valid
        ? el('span', {class:'chip status-COMPLETED', text:'معتبر'})
        : el('span', {class:'chip status-FAILED', title: r.error || '', text:'نامعتبر'})),
      el('td', {}, ratingCell(r)));
  }

  function load(){
    clear(body); body.appendChild(loadingBox());
    api('/benchmarks').then(function(runs){
      clear(body);
      if (!runs.length){
        body.appendChild(emptyBox('📈', 'هنوز اجرایی نیست', 'یک یا چند مسیر را بالا اجرا کن تا مقایسه شکل بگیرد.'));
        return;
      }
      var wrap = el('div', {class:'table-wrap'});
      wrap.appendChild(el('table', {},
        el('thead', {}, el('tr', {}, ['ارائه‌دهنده','مدل','تلاش','ورودی','خروجی','هزینه','زمان','schema','امتیاز']
          .map(function(h){ return el('th', {text:h}); }))),
        el('tbody', {}, runs.map(benchmarkRow))));
      body.appendChild(wrap);
    }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message, load)); });
  }
  load();
}

/* ============================== boot ============================== */

function boot(){
  root = document.getElementById('page-root');
  document.querySelectorAll('.nav-item').forEach(function(b){
    b.addEventListener('click', function(){ go(b.dataset.page); });
  });
  window.addEventListener('hashchange', route);
  ensureUser().then(route).catch(function(e){
    clear(root);
    root.appendChild(errorBox('اتصال به سرور برقرار نشد: ' + e.message, boot));
  });
}

document.addEventListener('DOMContentLoaded', boot);
})();
