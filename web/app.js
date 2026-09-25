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
var PROVIDERS = ['anthropic','claude_account','openai'];

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

var PAGES = ['dashboard','projects','agents','settings','observability'];
var root;

function setNav(page){
  document.querySelectorAll('.nav-item').forEach(function(b){
    b.classList.toggle('active', b.dataset.page === page);
  });
}

function route(){
  var hash = (location.hash || '#dashboard').slice(1);
  var parts = hash.split('/');
  var page = parts[0];
  if (PAGES.indexOf(page) === -1) page = 'dashboard';
  setNav(page);
  clear(root);
  if (page === 'dashboard') renderDashboard(root);
  else if (page === 'projects' && parts[1]) renderProjectDetail(root, Number(parts[1]));
  else if (page === 'projects') renderProjectsList(root);
  else if (page === 'agents' && parts[1]) renderAgentDetail(root, decodeURIComponent(parts[1]));
  else if (page === 'agents') renderAgentsList(root);
  else if (page === 'settings') renderSettings(root);
  else if (page === 'observability') renderObservability(root);
}

function go(hash){ location.hash = hash; }

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
      tabBtn('overview', 'بررسی'), tabBtn('tasks', 'وظایف'), tabBtn('events', 'رویدادها'), tabBtn('usage', 'مصرف'));
    body.appendChild(tabs);
    var panel = el('div', {class:'stack'});
    body.appendChild(panel);
    if (tab === 'overview') drawOverview(panel);
    else if (tab === 'tasks') drawTasks(panel);
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
    return card;
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
    return row;
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

  reload().catch(function(e){ clear(body); body.appendChild(errorBox(e.message, function(){ route(); })); });
}

/* ============================== agents ============================== */

function renderAgentsList(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:'ایجنت‌ها'})));
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
  var body = el('div', {class:'stack'});
  root.appendChild(body);
  body.appendChild(loadingBox());

  Promise.all([api('/agents/' + encodeURIComponent(name)), api('/skills')]).then(function(r){
    var agent = r[0], allSkills = r[1];
    clear(body);
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
  }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message)); });
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
      var curAccess = getPath(overrides, 'model_access');
      var overrideChk = el('input', {type:'checkbox'});
      overrideChk.checked = curAccess !== undefined;
      var sw = el('label', {class:'switch'}, el('input', {type:'checkbox'}), el('span', {class:'track'}));
      var swInput = sw.querySelector('input');
      var effective = curAccess !== undefined ? curAccess : resolved.model_access;
      swInput.checked = effective === 'claude_account';
      function syncSwitchDisabled(){ swInput.disabled = !overrideChk.checked; }
      syncSwitchDisabled();
      overrideChk.addEventListener('change', function(){
        syncSwitchDisabled();
        if (!overrideChk.checked) unsetPath(overrides, 'model_access');
        else setPath(overrides, 'model_access', swInput.checked ? 'claude_account' : 'api_key');
      });
      swInput.addEventListener('change', function(){
        setPath(overrides, 'model_access', swInput.checked ? 'claude_account' : 'api_key');
      });
      models.appendChild(el('div', {class:'card stack', style:'background:var(--surface-2)'},
        el('div', {class:'toggle'}, overrideChk, el('span', {text:'بازنویسی نحوه‌ی دسترسی به مدل در این لایه'})),
        el('div', {class:'toggle'}, sw, el('span', {}, el('b', {text:'کلید API'}), ' / ', el('b', {text:'حساب Claude خودم'})))
      ));
    }

    var budget = el('fieldset', {}, el('legend', {text:'بودجه و محدودیت‌ها'}),
      numberField('budget.project_usd', 'بودجه‌ی پروژه (دلار)', '0.01'),
      numberField('budget.task_usd', 'بودجه‌ی هر وظیفه (دلار)', '0.01'),
      numberField('budget.max_output_tokens', 'حداکثر توکن خروجی', '1'),
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
      el('thead', {}, el('tr', {}, ['نقش','ارائه‌دهنده','مدل','ورودی','خروجی','هزینه','وضعیت','زمان'].map(function(h){ return el('th', {text:h}); }))),
      el('tbody', {}, calls.map(function(c){
        return el('tr', {},
          el('td', {text: c.role}), el('td', {text: c.provider}), el('td', {text: c.model}),
          el('td', {text: fmtNum(c.input_tokens)}), el('td', {text: fmtNum(c.output_tokens)}),
          el('td', {text: fmtMoney(c.cost_usd)}),
          el('td', {}, c.ok ? el('span', {class:'chip', text:'موفق'}) : el('span', {class:'chip risk-high', text:'ناموفق'})),
          el('td', {text: fmtTime(c.created_at)}));
      })));
    wrap.appendChild(table);
    callCard.appendChild(wrap);
    body.appendChild(callCard);
  }
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
