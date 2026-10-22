(function(){
'use strict';

/* ============================== constants ============================== */

var STAGES = ['IDEA','DISCOVERY','VALIDATION','PRODUCT_DEFINITION','BUILDING','REVIEW','LAUNCH','MEASUREMENT','ITERATION'];
var MODEL_ROLES = ['manager','research','coding','cheap'];
var PROVIDERS = ['anthropic','claude_account','ollama','openai'];
var MEMORY_CATEGORIES = ['brief','decisions','research','customer','strategy','product','technical','experiments','learning'];
var INTERACTION_POLICIES = ['step_by_step','milestone','delegated_bounded'];
var QUALITY_TARGETS = ['prototype','production_candidate'];
var OUTPUT_LOCALES = ['auto','fa','en','tr'];
var AUDIENCE_LEVELS = ['ordinary','professional','developer'];

/* ============================== i18n (web/i18n.js) ============================== */

var t = I18N.t;
// A value's label in the UI language (`stage.IDEA`, `status.READY`, ...), or the value itself when it has none.
function lbl(group, value){
  if (value === null || value === undefined || value === '') return '—';
  var key = group + '.' + value;
  return I18N.has(key) ? t(key) : String(value);
}

/* ============================== api client ============================== */

// Our own errors send `detail` as a string; FastAPI's request validation sends a list of {loc, msg}.
function detailText(detail){
  if (!Array.isArray(detail)) return String(detail);
  return detail.map(function(d){
    if (!d || typeof d !== 'object') return String(d);
    var loc = (d.loc || []).slice();
    if (loc[0] === 'body') loc.shift();
    return (loc.length ? loc.join('.') + ': ' : '') + (d.msg || '');
  }).join('; ');
}

// Codes whose English `detail` says why (which field, which step): the reason is never hidden, only the code is.
var REASON_CODES = ['invalid_request', 'settings_invalid', 'request_refused', 'agent_invalid', 'transition_refused',
  'unknown_dependencies', 'provider_unavailable', 'budget_exceeded'];

// The API sends {detail, code, params} (app/errors.py): the owner reads the code's text in their language. The English
// detail follows it when it carries the reason, or for whoever asked to see technical details; a code this UI has no
// text for shows the detail itself.
function errorText(status, data){
  var code = data && data.code;
  var detail = data && data.detail !== undefined && data.detail !== null ? detailText(data.detail) : '';
  if (!code || !I18N.has('err.' + code)) return detail || (t('err.error') + ' (' + status + ')');
  var params = {};
  var given = (data && data.params) || {};
  for (var k in given) params[k] = given[k];
  if (code === 'not_found') params.resource = lbl('resource', given.resource || 'item');
  var text = t('err.' + code, params);
  if (detail && (REASON_CODES.indexOf(code) >= 0 || showCode())) text += ' (' + t('err.technical') + ': ' + detail + ')';
  return text;
}

function api(path, opts){
  opts = opts || {};
  var init = {method: opts.method || 'GET', headers:{}};
  if (opts.json !== undefined){
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.json);
  }
  return fetch(path, init).then(null, function(){
    var err = new Error(t('err.network'));
    err.status = 0;
    throw err;
  }).then(function(res){
    return res.text().then(function(text){
      var data = null;
      try { data = text ? JSON.parse(text) : null; } catch(e){}
      if (!res.ok){
        if (res.status === 401 && data && data.code === 'unauthenticated' && STATE.userId !== null){
          STATE.userId = null;
          setTimeout(boot, 0);  // signed out elsewhere or expired: the sign-in screen
        }
        var msg = errorText(res.status, data);
        var err = new Error(msg);
        err.status = res.status;
        err.code = data && data.code;
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
function fmtNum(n){ return I18N.fmtNum(n); }
function fmtTime(iso){ return I18N.fmtTime(iso); }
function statusChip(status){ return el('span', {class:'chip status-' + status, text: lbl('status', status)}); }
function riskChip(risk){
  if (risk === 'low') return null;
  return el('span', {class:'chip risk-' + risk, text: lbl('risk', risk)});
}
function loadingBox(text){
  return el('div', {class:'loading-state'}, el('span', {class:'spin'}), el('span', {text: text || t('common.loading')}));
}
function errorBox(message, retry){
  var b = el('div', {class:'error-state'}, el('div', {class:'ic', text:'⚠️'}), el('b', {text:t('common.problem')}),
    el('span', {text: message || t('common.server_error')}));
  if (retry) b.appendChild(el('button', {class:'btn sm', style:'margin-top:6px', onclick: retry}, t('common.retry')));
  return b;
}
function emptyBox(icon, title, sub){
  return el('div', {class:'empty-state'}, el('div', {class:'ic', text: icon}), el('b', {text: title}),
    sub ? el('span', {text: sub}) : null);
}

/* ============================== app state ============================== */

var STATE = { userId: null, me: null, workspaces: [], projects: [], prefs: null };

// The owner's display preferences (GET/PATCH /users/{id}/preferences). Display only: nothing here changes what the
// system may do. Until the server answers, the effective values are the defaults.
function prefs(){
  return (STATE.prefs && STATE.prefs.effective) || {ui_locale: I18N.locale(), audience_level: 'ordinary', code_visibility: 'hidden'};
}
function audience(){ return prefs().audience_level; }
function showCode(){ return prefs().code_visibility === 'visible'; }

function loadPreferences(){
  return api('/users/' + STATE.userId + '/preferences').then(function(p){
    STATE.prefs = p;
    if (p.ui_locale) I18N.setLocale(p.ui_locale);  // the account's choice wins over this browser's
    else if (I18N.storedLocale()) savePreferences({ui_locale: I18N.storedLocale()}).catch(function(){});
    return p;
  });
}
function savePreferences(patch){
  return api('/users/' + STATE.userId + '/preferences', {method:'PATCH', json: patch}).then(function(p){
    STATE.prefs = p;
    return p;
  });
}
// Changing the language re-draws the page in place: answers, approvals and the stage live on the server (the agent
// interview's answers in this tab's sessionStorage), so nothing is reset.
// Returns false when nothing changed (the owner kept unsaved input, or `loc` isn't offered); the caller resets its picker.
function changeLanguage(loc){
  if (loc === I18N.locale()) return true;
  if (hasUnsavedInput() && !window.confirm(t('lang.unsaved_confirm'))){
    syncLanguagePicker();
    return false;
  }
  if (!I18N.setLocale(loc)){
    syncLanguagePicker();
    return false;
  }
  syncLanguagePicker();
  route();
  savePreferences({ui_locale: loc}).catch(function(){ notify(t('lang.saved_locally')); });
  return true;
}

// A form control's value when the owner first focused it (`focusin` in boot): typed and not yet saved = changed since.
// Re-drawing a page replaces its controls, so a saved form never counts; the preferences card saves at once.
function hasUnsavedInput(){
  var controls = root ? root.querySelectorAll('input, textarea, select') : [];
  for (var i = 0; i < controls.length; i++){
    var c = controls[i];
    if (c._afOriginal !== undefined && c.value !== c._afOriginal) return true;
  }
  return false;
}
function rememberOriginal(e){
  var c = e.target;
  if (!c || !/^(INPUT|TEXTAREA|SELECT)$/.test(c.tagName) || c.closest('#prefs-card')) return;
  if (c._afOriginal === undefined) c._afOriginal = c.value;
}

// A short notice above the page (role=status, read by screen readers), gone after a few seconds.
function notify(text){
  var box = document.getElementById('notice');
  if (!box) return;
  box.textContent = text;
  box.hidden = false;
  clearTimeout(notify._timer);
  notify._timer = setTimeout(function(){ box.hidden = true; }, 6000);
}
function syncLanguagePicker(){
  var picker = document.getElementById('lang-picker');
  if (picker) picker.value = I18N.locale();
}
function setupLanguagePicker(){
  var picker = document.getElementById('lang-picker');
  if (!picker) return;
  clear(picker);
  I18N.LOCALES.forEach(function(loc){ picker.appendChild(el('option', {value: loc, lang: loc}, I18N.NATIVE_NAMES[loc])); });
  picker.value = I18N.locale();
  picker.addEventListener('change', function(){ changeLanguage(picker.value); });  // resets itself when refused
}

// First entry: the owner picks the language explicitly (docs/FACTORY_UI_SPEC.md); nothing else is asked first.
function renderLanguageChooser(root, onDone, toAccount){
  function inLang(loc, node){ node.setAttribute('lang', loc); node.setAttribute('dir', I18N.isRtl(loc) ? 'rtl' : 'ltr'); return node; }
  var titles = I18N.LOCALES.map(function(loc){
    return inLang(loc, el('span', {class:'lang-title-part', text: I18N.tIn(loc, 'lang.choose_title')}));
  });
  var buttons = I18N.LOCALES.map(function(loc){
    return inLang(loc, el('button', {class:'btn lang-choice', onclick: function(){
      I18N.setLocale(loc);
      syncLanguagePicker();
      if (!toAccount){ onDone(); return; }  // not signed in yet: this browser only, saved to the account later
      savePreferences({ui_locale: loc}).then(onDone, function(){ onDone(); notify(t('lang.saved_locally')); });
    }}, I18N.NATIVE_NAMES[loc]));
  });
  root.appendChild(el('div', {class:'card stack lang-chooser', role:'dialog', 'aria-labelledby':'lang-title'},
    el('h2', {id:'lang-title'}, titles),
    el('p', {class:'muted', text: t('lang.choose_sub')}),
    el('div', {class:'btn-row lang-choices'}, buttons)));
}

// Signed in (app/auth.py): an HttpOnly session cookie this script never sees. Resolves to the account, or to null
// when this browser must sign in (the sign-in screen is shown by boot()).
function ensureUser(){
  return api('/auth/state').then(function(st){
    if (st.signed_in) return api('/auth/me');
    var oldId = localStorage.getItem('af_user_id');  // an account from before login existed: claim it once
    if (oldId) return api('/auth/claim', {method:'POST', json:{user_id: Number(oldId)}}).catch(function(){ return null; });
    if (!st.accounts_exist){  // the server's first visitor: its admin
      var email = 'owner-' + Math.random().toString(36).slice(2, 10) + '@agent-factory.local';
      return api('/users', {method:'POST', json:{email: email, name:t('common.owner_name')}});
    }
    return null;
  }).then(function(me){
    if (!me) return null;
    STATE.userId = me.id;
    STATE.me = me;
    try { localStorage.setItem('af_user_id', me.id); } catch(e){}
    return me;
  });
}

// Shown when this browser isn't signed in: a one-time code from a signed-in device (or, on the server's own
// machine or with the owner token, the account this browser had before login existed).
function renderSignIn(root){
  clear(root);
  var code = el('input', {type:'text', id:'login-code', autocomplete:'one-time-code', dir:'ltr', placeholder:'XXXX-XXXX-XXXX'});
  var status = el('p', {class:'err muted', role:'status'});
  var go1 = el('button', {class:'btn', text: t('auth.sign_in')});
  go1.addEventListener('click', function(){
    if (!code.value.trim()){ status.textContent = t('auth.code_needed'); return; }
    go1.disabled = true; status.textContent = '';
    api('/auth/login', {method:'POST', json:{code: code.value.trim()}}).then(function(){ boot(); })
      .catch(function(e){ status.textContent = e.message; go1.disabled = false; });
  });
  var token = el('input', {type:'password', autocomplete:'off', dir:'ltr'});
  var userId = el('input', {type:'number', min:'1', dir:'ltr'});
  var oldId = null;
  try { oldId = localStorage.getItem('af_user_id'); } catch(e){}
  if (oldId) userId.value = oldId;
  var claimStatus = el('p', {class:'err muted', role:'status'});
  var claimBtn = el('button', {class:'btn ghost sm', text: t('auth.claim')});
  claimBtn.addEventListener('click', function(){
    claimBtn.disabled = true; claimStatus.textContent = '';
    api('/auth/claim', {method:'POST', json:{user_id: Number(userId.value), owner_token: token.value || null}})
      .then(function(){ boot(); })
      .catch(function(e){ claimStatus.textContent = e.message; claimBtn.disabled = false; });
  });
  root.appendChild(el('div', {class:'card stack lang-chooser', role:'dialog', 'aria-labelledby':'signin-title'},
    el('h2', {id:'signin-title', text: t('auth.title')}),
    el('p', {class:'muted', text: t('auth.code_help')}),
    el('label', {class:'f'}, t('auth.code'), code),
    el('div', {class:'btn-row'}, go1), status,
    el('details', {class:'advanced'}, el('summary', {text: t('auth.claim_title')}),
      el('p', {class:'muted', text: t('auth.claim_help')}),
      el('label', {class:'f'}, t('auth.account_number'), userId),
      el('label', {class:'f'}, t('auth.owner_token'), token),
      el('div', {class:'btn-row'}, claimBtn), claimStatus)));
}

function fetchWorkspaces(){ return api('/workspaces').then(function(ws){ STATE.workspaces = ws; return ws; }); }
function fetchProjects(){ return api('/projects?owner_id=' + STATE.userId).then(function(ps){ STATE.projects = ps; return ps; }); }

/* ============================== router ============================== */

var PAGES = ['dashboard','inbox','projects','agents','build','benchmark','settings','observability'];
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
  else if (page === 'agents' && parts[1] === 'new') startBuild(root);
  else if (page === 'agents' && parts[1] === 'blueprint') renderAgentIntake(root);
  else if (page === 'build' && parts[1]) renderBuild(root, Number(parts[1]));
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
  var note = el('input', {type:'text', placeholder:t('common.note_optional'), style:'flex:1 1 160px'});
  var status = el('span', {class:'muted'});
  var up = el('button', {class:'thumb', type:'button', text:'👍'});
  var down = el('button', {class:'thumb', type:'button', text:'👎'});
  function submit(rating){
    up.classList.toggle('active-up', rating === 'up');
    down.classList.toggle('active-down', rating === 'down');
    status.textContent = '…';
    api('/feedback', {method:'POST', json:{
      project_id: projectId, task_id: taskId, agent: agent, rating: rating, note: note.value.trim() || null
    }}).then(function(){ status.textContent = t('common.saved_feedback'); }).catch(function(e){ status.textContent = e.message; });
  }
  up.addEventListener('click', function(){ submit('up'); });
  down.addEventListener('click', function(){ submit('down'); });
  wrap.appendChild(el('span', {class:'muted', text:t('feedback.question')}));
  wrap.appendChild(up); wrap.appendChild(down); wrap.appendChild(note); wrap.appendChild(status);
  return wrap;
}

/* ============================== inbox ============================== */

function renderInbox(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:t('nav.inbox')})));
  var body = el('div', {class:'stack'});
  root.appendChild(body);

  function load(){
    clear(body); body.appendChild(loadingBox());
    api('/inbox').then(function(items){
      clear(body);
      refreshInboxBadge();
      if (!items.length){
        body.appendChild(emptyBox('📥', t('inbox.empty_title'), t('inbox.empty_sub')));
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
    el('span', {class:'kind', text: lbl('inbox.kind', item.kind)}),
    el('button', {class:'chip', style:'cursor:pointer;border:0', onclick:function(){ go('projects/' + item.project_id); }}, item.project_title)));
  card.appendChild(el('p', {}, el('b', {text: I18N.has('inbox.head.' + item.kind) ? t('inbox.head.' + item.kind) : ''}), item.challenge));

  if (item.kind === 'blocked' || item.kind === 'waiting'){
    var waiting = (item.options || [])[0] || {};
    card.appendChild(el('div', {class:'row'}, el('span', {class:'chip', text: t('task.owner', {owner: waiting.owner || '—'})}),
      riskChip(waiting.risk), el('span', {class:'chip', text: lbl('treason', waiting.reason)})));
    if (item.why) card.appendChild(el('p', {class:'muted', text: item.why}));
    card.appendChild(el('div', {class:'btn-row'},
      el('button', {class:'btn sm', onclick:function(){ go('projects/' + item.project_id); }}, t('inbox.go_to_project'))));
  } else if (item.kind === 'ready'){
    var task = (item.options || [])[0] || {};
    card.appendChild(el('div', {class:'row'}, el('span', {class:'chip', text: t('task.owner', {owner: task.owner || '—'})}), riskChip(task.risk)));
    if (item.why) card.appendChild(el('p', {class:'muted'}, el('b', {text:t('inbox.goal_label')}), item.why));
    var startStatus = el('p', {class:'err muted'});
    var startBtn = el('button', {class:'btn', text:t('inbox.start')});
    // "Start" = ask the manager for this task's checkpoint (never a bare READY -> RUNNING jump, which
    // would skip the decision gate): auto-decided in automatic mode, otherwise it becomes a decision card.
    startBtn.addEventListener('click', function(){
      startBtn.disabled = true; startStatus.textContent = '';
      startBtn.textContent = t('checkpoint.preparing');
      api('/projects/' + item.project_id + '/tasks/' + item.task_id + '/checkpoint', {method:'POST'}).then(function(r){
        if (r && r.auto_decided){
          clear(card);
          card.appendChild(el('p', {text:t('checkpoint.auto_decided')}));
          card.appendChild(el('div', {class:'btn-row'},
            el('button', {class:'btn sm', onclick:function(){ go('projects/' + item.project_id); }}, t('inbox.go_to_project'))));
          refreshInboxBadge();
          return;
        }
        onDone();
      }).catch(function(e){ startStatus.textContent = e.message; startBtn.disabled = false; startBtn.textContent = t('inbox.start'); });
    });
    card.appendChild(el('div', {class:'btn-row'}, startBtn));
    card.appendChild(startStatus);
  } else if (item.kind === 'plan'){
    var ol = el('div', {class:'stack', style:'gap:6px'});
    (item.options || []).forEach(function(o){
      ol.appendChild(el('div', {class:'row', style:'justify-content:space-between'},
        el('span', {text:o.title}), el('span', {class:'row'}, el('span', {class:'chip', text:o.owner || '?'}), riskChip(o.risk))));
    });
    card.appendChild(ol);
    var fb = el('textarea', {placeholder:t('plan.reject_placeholder')});
    var status = el('p', {class:'err muted'});
    var approveBtn = el('button', {class:'btn', text:t('plan.approve')});
    var rejectBtn = el('button', {class:'btn ghost', text:t('plan.reject')});
    approveBtn.addEventListener('click', function(){
      approveBtn.disabled = true;
      api('/projects/' + item.project_id + '/plan/approve', {method:'POST'}).then(onDone)
        .catch(function(e){ status.textContent = e.message; approveBtn.disabled = false; });
    });
    rejectBtn.addEventListener('click', function(){
      var f = fb.value.trim();
      if (!f){ status.textContent = t('plan.reject_needs_feedback'); return; }
      rejectBtn.disabled = true;
      api('/projects/' + item.project_id + '/plan/reject', {method:'POST', json:{feedback:f}}).then(onDone)
        .catch(function(e){ status.textContent = e.message; rejectBtn.disabled = false; });
    });
    card.appendChild(el('label', {class:'f'}, t('plan.feedback'), fb));
    card.appendChild(el('div', {class:'btn-row'}, approveBtn, rejectBtn));
    card.appendChild(status);
  } else {
    var picked = item.recommended || 0;
    var group = el('div', {class:'stack', style:'gap:8px', role:'radiogroup'});
    (item.options || []).forEach(function(o, i){
      var opt = el('button', {class:'opt', role:'radio', 'aria-checked': String(i === picked)},
        el('span', {}, el('b', {}, o.title, i === item.recommended ? el('span', {class:'rec', text:t('checkpoint.recommended')}) : null),
          el('small', {text:o.tradeoff})));
      opt.addEventListener('click', function(){
        picked = i;
        group.querySelectorAll('.opt').forEach(function(x, xi){ x.setAttribute('aria-checked', String(xi === i)); });
      });
      group.appendChild(opt);
    });
    card.appendChild(group);
    if (item.why) card.appendChild(el('p', {class:'muted'}, el('b', {text:t('checkpoint.why')}), item.why));
    var note = el('textarea', {placeholder:t('common.note_optional')});
    card.appendChild(el('label', {class:'f'}, t('common.note'), note));
    var status2 = el('p', {class:'err muted'});
    var decideBtn = el('button', {class:'btn', text:t('checkpoint.decide')});
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
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:t('nav.dashboard')})));
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
    body.appendChild(emptyBox('📊', t('dashboard.empty_title'), t('dashboard.empty_sub')));
    body.appendChild(el('button', {class:'btn', onclick:function(){ go('projects'); }}, t('dashboard.create_first')));
    return;
  }
  var totalCost = 0, totalBudget = 0;
  usages.forEach(function(u){ if (u){ totalCost += u.cost_usd; totalBudget += u.budget_usd; } });

  var stats = el('div', {class:'grid grid-3'},
    el('div', {class:'card stat'}, el('b', {text: fmtNum(projects.length)}), el('span', {text:t('dashboard.project_count')})),
    el('div', {class:'card stat'}, el('b', {text: fmtMoney(totalCost)}), el('span', {text:t('dashboard.total_cost')})),
    el('div', {class:'card stat'}, el('b', {text: fmtMoney(totalBudget)}), el('span', {text:t('dashboard.total_budget')}))
  );
  body.appendChild(stats);

  var recent = el('div', {class:'card stack'}, el('h3', {text:t('dashboard.recent_projects')}));
  var list = el('div', {class:'list'});
  projects.slice().reverse().slice(0, 5).forEach(function(p){
    list.appendChild(el('button', {class:'list-item', onclick:function(){ go('projects/' + p.id); }},
      el('div', {class:'top'}, el('b', {text:p.title}), el('span', {class:'chip', text: lbl('stage', p.stage)})),
      el('span', {class:'muted', text: p.goal})
    ));
  });
  recent.appendChild(list);
  body.appendChild(recent);

  var evCard = el('div', {class:'card stack'}, el('h3', {text:t('dashboard.recent_events')}));
  if (!events.length) evCard.appendChild(el('p', {class:'muted', text:t('common.no_events')}));
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
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:t('nav.projects')}),
    el('div', {class:'spacer', style:'flex:1'}),
    el('button', {class:'btn sm', id:'new-project-btn'}, t('projects.new_button'))));
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
        body.appendChild(emptyBox('📁', t('projects.empty_title'), t('projects.empty_sub')));
        return;
      }
      var list = el('div', {class:'list'});
      STATE.projects.slice().reverse().forEach(function(p){
        list.appendChild(el('button', {class:'list-item', onclick:function(){ go('projects/' + p.id); }},
          el('div', {class:'top'}, el('b', {text:p.title}), el('span', {class:'chip', text: lbl('stage', p.stage)})),
          el('span', {class:'muted', text:p.goal}),
          el('div', {class:'row'}, el('span', {class:'chip', text: lbl('mode', p.mode)}),
            p.paused ? el('span', {class:'chip', text:t('projects.paused')}) : null)
        ));
      });
      body.appendChild(list);
    }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message, load)); });
  }
  load();
}

function projectCreateForm(onDone){
  var title = el('input', {type:'text', placeholder:t('projects.title_placeholder')});
  var goal = el('textarea', {placeholder:t('projects.goal_placeholder')});
  var mode = el('select', {},
    el('option', {value:'automatic'}, lbl('mode', 'automatic')),
    el('option', {value:'manual_learning'}, lbl('mode', 'manual_learning')));
  var budget = el('input', {type:'number', min:'0', step:'0.1', placeholder:t('projects.budget_placeholder')});
  var ws = el('select', {}, el('option', {value:''}, t('projects.no_workspace')));
  STATE.workspaces.forEach(function(w){ ws.appendChild(el('option', {value:w.id}, w.name)); });
  var status = el('p', {class:'err muted'});
  var submitBtn = el('button', {class:'btn'}, t('projects.create'));

  submitBtn.addEventListener('click', function(){
    var titleText = title.value.trim(), g = goal.value.trim();
    if (!titleText || !g){ status.textContent = t('projects.fill_title_goal'); return; }
    submitBtn.disabled = true; status.textContent = '';
    api('/projects', {method:'POST', json:{
      owner_id: STATE.userId, title: titleText, goal: g, mode: mode.value,
      workspace_id: ws.value ? Number(ws.value) : null,
      budget: budget.value ? Number(budget.value) : null
    }}).then(function(p){ onDone(); go('projects/' + p.id); })
      .catch(function(e){ status.textContent = e.message; submitBtn.disabled = false; });
  });

  return el('div', {class:'card stack', style:'margin-bottom:14px'},
    el('h3', {text:t('projects.new_title')}),
    el('label', {class:'f'}, t('common.title'), title),
    el('label', {class:'f'}, t('common.goal'), goal),
    el('div', {class:'grid grid-2'},
      el('label', {class:'f'}, t('common.mode'), mode),
      el('label', {class:'f'}, t('projects.budget_usd'), budget)),
    el('label', {class:'f'}, t('common.workspace'), ws),
    el('div', {class:'row'}, submitBtn, status)
  );
}

/* ============================== project detail ============================== */

function renderProjectDetail(root, projectId){
  root.appendChild(el('div', {class:'page-head'},
    el('button', {class:'btn ghost sm', onclick:function(){ go('projects'); }}, t('projects.back')),
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
      tabBtn('overview', t('tab.overview')), tabBtn('tasks', t('tab.tasks')), tabBtn('chat', t('tab.chat')),
      tabBtn('memory', t('tab.memory')), tabBtn('events', t('common.events')), tabBtn('usage', t('tab.usage')));
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
      el('div', {class:'row'}, el('span', {class:'chip', text: lbl('stage', project.stage)}),
        el('span', {class:'chip', text: lbl('mode', project.mode)}),
        project.paused ? el('span', {class:'chip', text:t('projects.paused')}) : null),
      el('p', {text: project.goal}),
      el('div', {class:'btn-row'},
        stageButton(),
        el('button', {class:'btn ghost sm', onclick: onTogglePause}, project.paused ? t('project.resume') : t('project.pause'))
      )
    ));

    var pendingPlanTasks = tasks.filter(function(task){ return task.status === 'CREATED'; });
    var lastPlanEvent = null;
    events.forEach(function(e){ if (e.type === 'plan.proposed') lastPlanEvent = e; });
    var afterPlanRequested = lastPlanEvent ? events.filter(function(e){ return e.id > lastPlanEvent.id && e.type === 'specialist.requested'; }) : [];

    if (pendingPlanTasks.length && lastPlanEvent){
      panel.appendChild(planReviewCard(lastPlanEvent, pendingPlanTasks, afterPlanRequested));
    } else {
      var planStatus = el('p', {class:'err muted'});
      var planBtn = el('button', {class:'btn', text: tasks.length ? t('plan.replan') : t('plan.make')});
      planBtn.addEventListener('click', function(){
        planBtn.disabled = true; planStatus.textContent = '';
        api('/projects/' + projectId + '/plan', {method:'POST'})
          .then(function(){ return reload(); })
          .catch(function(e){ planStatus.textContent = e.message; planBtn.disabled = false; });
      });
      panel.appendChild(el('div', {class:'card stack'}, el('h3', {text:t('plan.title')}),
        el('p', {class:'muted', text:t('plan.explain')}),
        el('div', {class:'btn-row'}, planBtn), planStatus));
    }
  }

  function stageButton(){
    var idx = STAGES.indexOf(project.stage);
    var next = (project.stage === 'ITERATION') ? 'DISCOVERY' : (idx >= 0 && idx + 1 < STAGES.length ? STAGES[idx + 1] : null);
    if (!next) return null;
    var b = el('button', {class:'btn sm', text: t('project.advance_to', {stage: lbl('stage', next)})});
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
    var card = el('div', {class:'card stack'}, el('h3', {text:t('plan.pending')}),
      el('p', {text: planEvent.payload.understanding || ''}));
    if ((planEvent.payload.assumptions || []).length){
      var a = el('div', {}, el('p', {class:'muted', text:t('plan.assumptions')}));
      planEvent.payload.assumptions.forEach(function(x){ a.appendChild(el('p', {class:'muted', text:'• ' + x})); });
      card.appendChild(a);
    }
    var ol = el('ol', {class:'list', style:'padding-inline-start:0'});
    pendingTasks.forEach(function(task){
      ol.appendChild(el('div', {class:'list-item', style:'cursor:default'},
        el('div', {class:'top'}, el('b', {text:task.title}), el('span', {class:'chip', text:task.owner || '?'})),
        riskChip(task.risk)
      ));
    });
    card.appendChild(ol);
    if (requestedSpecs.length){
      var rs = el('div', {}, el('p', {class:'muted', text:t('plan.requested_specialists')}));
      requestedSpecs.forEach(function(e){ rs.appendChild(el('p', {class:'muted', text:'• ' + e.payload.title + ' (' + e.payload.agent + ')'})); });
      card.appendChild(rs);
    }
    var fb = el('textarea', {placeholder:t('plan.reject_placeholder')});
    var status = el('p', {class:'err muted'});
    var approveBtn = el('button', {class:'btn', text:t('plan.approve')});
    var rejectBtn = el('button', {class:'btn ghost', text:t('plan.reject')});
    approveBtn.addEventListener('click', function(){
      approveBtn.disabled = true;
      api('/projects/' + projectId + '/plan/approve', {method:'POST'}).then(reload)
        .catch(function(e){ status.textContent = e.message; approveBtn.disabled = false; });
    });
    rejectBtn.addEventListener('click', function(){
      var f = fb.value.trim();
      if (!f){ status.textContent = t('plan.reject_needs_feedback'); return; }
      rejectBtn.disabled = true;
      api('/projects/' + projectId + '/plan/reject', {method:'POST', json:{feedback:f}}).then(reload)
        .catch(function(e){ status.textContent = e.message; rejectBtn.disabled = false; });
    });
    card.appendChild(el('label', {class:'f'}, t('plan.reject_feedback_label'), fb));
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
    var input = el('textarea', {placeholder:t('chat.placeholder')});
    var sendBtn = el('button', {class:'btn', text:t('chat.send')});
    var status = el('p', {class:'err muted'});
    panel.appendChild(el('div', {class:'card stack'},
      el('div', {class:'chat-input-row'}, input, sendBtn), status));

    function suggestedActionButton(action){
      var m = /^checkpoint:(\d+)$/.exec(action);
      var b = el('button', {class:'btn sm'});
      if (action === 'plan'){
        b.textContent = tasks.length ? t('plan.replan') : t('plan.make');
        b.addEventListener('click', function(){
          b.disabled = true;
          api('/projects/' + projectId + '/plan', {method:'POST'})
            .then(function(){ tab = 'overview'; return reload(); })
            .catch(function(e){ alert(e.message); b.disabled = false; });
        });
      } else if (action === 'approve'){
        b.textContent = t('plan.approve');
        b.addEventListener('click', function(){
          b.disabled = true;
          api('/projects/' + projectId + '/plan/approve', {method:'POST'})
            .then(function(){ tab = 'overview'; return reload(); })
            .catch(function(e){ alert(e.message); b.disabled = false; });
        });
      } else if (m){
        var tid = Number(m[1]);
        b.textContent = t('chat.open_checkpoint');
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
        if (!msgs.length){ list.appendChild(el('p', {class:'muted', text:t('chat.empty')})); return; }
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
    if (!tasks.length){ panel.appendChild(emptyBox('🗂️', t('tasks.empty_title'), t('tasks.empty_sub'))); return; }
    var list = el('div', {class:'list'});
    tasks.forEach(function(task){ list.appendChild(taskRow(task)); });
    panel.appendChild(list);
  }

  function taskRow(task){
    var row = el('div', {class:'card stack'},
      el('div', {class:'top row', style:'justify-content:space-between'},
        el('b', {text:task.title}),
        el('div', {class:'row'}, statusChip(task.status), riskChip(task.risk))),
      el('p', {class:'muted', text: t('task.owner', {owner: task.owner || '—'}) + (task.depends_on.length ? ' · ' + t('task.depends_on', {ids: '#' + task.depends_on.join(t('list.sep') + '#')}) : '')})
    );
    if (task.reason && task.status !== 'BLOCKED') row.appendChild(el('p', {class:'muted', text: lbl('treason', task.reason)}));
    if (task.status === 'BLOCKED') row.appendChild(unblockArea(task));
    if (task.status === 'READY') row.appendChild(checkpointArea(task));
    if (task.status === 'RUNNING' || task.status === 'WAITING') row.appendChild(runPanel(task));
    // Not shown while RUNNING: a redo loop (COMPLETED -> READY -> RUNNING) keeps the old output
    // until this run finishes, and showing it next to the "اجرا" button would look like a fresh result.
    if (task.output && task.status !== 'RUNNING') row.appendChild(outputBox(task.output));
    return row;
  }

  function runArea(task){
    var holder = el('div', {class:'stack'});
    var btn = el('button', {class:'btn sm', text:t('tasks.run')});
    holder.appendChild(btn);
    btn.addEventListener('click', function(){
      btn.disabled = true;
      clear(holder);
      holder.appendChild(loadingBox(t('tasks.running')));
      api('/projects/' + projectId + '/tasks/' + task.id + '/run', {method:'POST'})
        .then(reload)
        .catch(function(e){
          clear(holder);
          holder.appendChild(errorBox(e.message));
          holder.appendChild(el('button', {class:'btn ghost sm', text:t('common.retry'),
            onclick:function(){ clear(holder); holder.appendChild(btn); btn.disabled = false; }}));
        });
    });
    return holder;
  }

  function unblockArea(task){
    var holder = el('div', {class:'stack'});
    var status = el('p', {class:'err muted'});
    var btn = el('button', {class:'btn sm', text:t('tasks.unblock')});
    btn.addEventListener('click', function(){
      btn.disabled = true; status.textContent = '';
      api('/projects/' + projectId + '/tasks/' + task.id + '/unblock', {method:'POST'})
        .then(reload).catch(function(e){ status.textContent = e.message; btn.disabled = false; });
    });
    holder.appendChild(el('p', {class:'muted', text:t('tasks.blocked_help', {owner: task.owner || '—'})}));
    holder.appendChild(el('div', {class:'btn-row'}, btn));
    holder.appendChild(status);
    return holder;
  }

  // A bounded run (app/runtime.py): one step at a time by a worker; this shows its steps and what it waits on, and
  // polls while a worker has it. A RUNNING task with no open run offers a new run or the one-shot run (runArea).
  var OPEN_RUN = {queued: true, running: true, waiting: true};

  function runStepText(s){
    var p = s.payload || {};
    if (s.kind === 'proposal') return lbl('raction', p.action) + (p.tool ? ' · ' + p.tool : '') + (p.reason ? ' — ' + p.reason : '');
    if (s.kind === 'observation'){
      if (p.refused) return (p.tool || '') + ' · ' + lbl('rrefused', p.refused);
      if (p.error) return lbl('treason', p.error);
      return (p.tool || '') + ' · ' + lbl('tstate', p.state) + (p.result ? ' — ' + JSON.stringify(p.result).slice(0, 300) : '');
    }
    if (s.kind === 'question') return p.text || '';
    if (s.kind === 'approval'){
      return p.kind === 'reconcile' ? t('run.reconcile_q', {tool: p.tool})
        : t('run.tool_q', {tool: p.tool}) + (p.input ? ' ' + JSON.stringify(p.input) : '');
    }
    if (s.kind === 'answer'){
      if (p.text) return p.text;
      if (p.reconciled) return p.happened ? t('run.happened') : t('run.not_happened');
      return p.approve ? t('run.approve') : t('run.deny');
    }
    if (s.kind === 'finish') return p.summary || '';
    if (s.kind === 'stop') return lbl('treason', p.reason);
    return '';
  }

  function runPanel(task){
    var holder = el('div', {class:'stack run-panel'});
    var base = '/projects/' + projectId + '/tasks/' + task.id + '/runs';
    var shown = null;  // the state last drawn

    function load(){
      api(base).then(function(runs){
        var run = runs[0];
        var state = run && OPEN_RUN[run.state] ? run.state : null;
        // The task moves with the run (RUNNING <-> WAITING, then COMPLETED/FAILED): redraw the page around it.
        if (shown && shown !== state && (state === 'waiting' || shown === 'waiting' || !state)){ reload(); return; }
        shown = state;
        if (state){ draw(run); return; }
        drawIdle(run);
      }).catch(function(e){ clear(holder); holder.appendChild(errorBox(e.message, load)); });
    }

    function act(button, path, body){
      button.disabled = true;
      return api(base + path, {method:'POST', json: body}).then(load)
        .catch(function(e){ button.disabled = false; alert(e.message); });
    }

    function drawIdle(last){
      clear(holder);
      if (last){
        holder.appendChild(el('p', {class:'muted'}, el('b', {text:t('run.last') + ': '}),
          lbl('rstate', last.state) + (last.reason ? ' · ' + lbl('treason', last.reason) : '')));
      }
      if (task.status !== 'RUNNING') return;
      var start = el('button', {class:'btn sm', text:t('run.start')});
      start.addEventListener('click', function(){ act(start, '', undefined); });
      holder.appendChild(el('div', {class:'btn-row'}, start));
      holder.appendChild(runArea(task));
    }

    function draw(run){
      clear(holder);
      holder.appendChild(el('div', {class:'row', style:'justify-content:space-between'},
        el('b', {text:t('run.title', {id: run.id})}),
        el('div', {class:'row'}, el('span', {class:'chip run-' + run.state, text: lbl('rstate', run.state)}),
          run.reason ? el('span', {class:'chip', text: lbl('treason', run.reason)}) : null)));
      holder.appendChild(el('p', {class:'muted', text:t('run.progress', {steps: run.steps_used, max: run.limits.max_steps,
        tools: run.tool_calls, max_tools: run.limits.max_tool_calls})}));
      var steps = el('ol', {class:'run-steps'});
      run.steps.forEach(function(s){
        steps.appendChild(el('li', {}, el('b', {text: lbl('rstep', s.kind) + ': '}), runStepText(s)));
      });
      if (run.steps.length) holder.appendChild(steps);

      var pending = run.pending || {};
      if (run.state === 'waiting' && pending.kind === 'question'){
        var answer = el('textarea', {placeholder:t('run.answer_placeholder')});
        var send = el('button', {class:'btn sm', text:t('run.send')});
        send.addEventListener('click', function(){
          if (!answer.value.trim()) return;
          act(send, '/' + run.id + '/respond', {text: answer.value.trim()});
        });
        holder.appendChild(answer);  // the question is the last step listed above
        holder.appendChild(el('div', {class:'btn-row'}, send));
      } else if (run.state === 'waiting' && pending.kind === 'tool_approval'){
        var yes = el('button', {class:'btn sm', text:t('run.approve')});
        var no = el('button', {class:'btn ghost sm', text:t('run.deny')});
        yes.addEventListener('click', function(){ act(yes, '/' + run.id + '/respond', {approve: true}); });
        no.addEventListener('click', function(){ act(no, '/' + run.id + '/respond', {approve: false}); });
        holder.appendChild(el('div', {class:'btn-row'}, yes, no));
      } else if (run.state === 'waiting' && pending.kind === 'reconcile'){
        var did = el('button', {class:'btn sm', text:t('run.happened')});
        var didnt = el('button', {class:'btn ghost sm', text:t('run.not_happened')});
        did.addEventListener('click', function(){ act(did, '/' + run.id + '/respond', {happened: true}); });
        didnt.addEventListener('click', function(){ act(didnt, '/' + run.id + '/respond', {happened: false}); });
        holder.appendChild(el('div', {class:'btn-row'}, did, didnt));
      }
      holder.appendChild(el('p', {class:'muted', text: run.state === 'waiting' ? t('run.waiting_hint') : t('run.working')}));
      var cancel = el('button', {class:'btn ghost sm', text:t('run.cancel')});
      cancel.addEventListener('click', function(){ act(cancel, '/' + run.id + '/cancel', undefined); });
      holder.appendChild(el('div', {class:'btn-row'}, cancel));
      if (run.state !== 'waiting'){
        setTimeout(function(){ if (holder.isConnected) load(); }, 1500);
      }
    }

    holder.appendChild(loadingBox());
    load();
    return holder;
  }

  function evidenceChip(type){
    return el('span', {class:'chip ev-' + String(type).toLowerCase(), text: lbl('evidence', type)});
  }

  function outputBox(output){
    var box = el('div', {class:'stack', style:'margin-top:8px'});
    if (output.summary) box.appendChild(el('p', {}, el('b', {text:t('output.summary')}), output.summary));
    if (output.findings && output.findings.length){
      var list = el('div', {class:'stack', style:'gap:6px'});
      output.findings.forEach(function(f){
        list.appendChild(el('div', {class:'row', style:'align-items:flex-start;gap:8px'},
          evidenceChip(f.type),
          el('span', {}, el('b', {text:f.claim}), el('small', {class:'muted', text:' — ' + f.basis}))));
      });
      box.appendChild(list);
    }
    if (output.lesson) box.appendChild(el('p', {class:'muted'}, el('b', {text:t('output.lesson')}), output.lesson));
    if (output.next) box.appendChild(el('p', {class:'muted'}, el('b', {text:t('output.next')}), output.next));
    return box;
  }

  function checkpointArea(task){
    var holder = el('div', {class:'stack'});
    var btn = el('button', {class:'btn sm', text:t('checkpoint.open')});
    holder.appendChild(btn);
    btn.addEventListener('click', function(){
      btn.disabled = true;
      clear(holder);
      holder.appendChild(loadingBox(t('checkpoint.preparing')));
      api('/projects/' + projectId + '/tasks/' + task.id + '/checkpoint', {method:'POST'}).then(function(r){
        clear(holder);
        if (r.auto_decided){
          holder.appendChild(el('p', {class:'muted', text:t('checkpoint.auto_decided')}));
          reload();
          return;
        }
        holder.appendChild(checkpointUI(task, r.checkpoint));
      }).catch(function(e){
        clear(holder);
        holder.appendChild(errorBox(e.message));
        holder.appendChild(el('button', {class:'btn ghost sm', text:t('common.retry'), onclick:function(){ clear(holder); holder.appendChild(btn); btn.disabled = false; }}));
      });
    });
    return holder;
  }

  function checkpointUI(task, cp){
    var picked = cp.recommended;
    var box = el('div', {class:'stack'});
    box.appendChild(el('p', {}, el('b', {text:t('checkpoint.challenge')}), cp.challenge));
    var group = el('div', {class:'stack', style:'gap:8px', role:'radiogroup'});
    cp.options.forEach(function(o, i){
      var opt = el('button', {class:'opt', role:'radio', 'aria-checked': String(i === picked)},
        el('span', {},
          el('b', {}, o.title, i === cp.recommended ? el('span', {class:'rec', text:t('checkpoint.recommended')}) : null),
          el('small', {text:o.tradeoff})));
      opt.addEventListener('click', function(){
        picked = i;
        group.querySelectorAll('.opt').forEach(function(x, xi){ x.setAttribute('aria-checked', String(xi === i)); });
      });
      group.appendChild(opt);
    });
    box.appendChild(group);
    box.appendChild(el('p', {class:'muted'}, el('b', {text:t('checkpoint.why')}), cp.why));
    var note = el('textarea', {placeholder:t('checkpoint.note_placeholder')});
    box.appendChild(el('label', {class:'f'}, t('common.note'), note));
    var status = el('p', {class:'err muted'});
    var decideBtn = el('button', {class:'btn', text:t('checkpoint.decide')});
    decideBtn.addEventListener('click', function(){
      decideBtn.disabled = true;
      api('/projects/' + projectId + '/tasks/' + task.id + '/decide', {method:'POST', json:{option:picked, note: note.value.trim() || null}})
        .then(reload).catch(function(e){ status.textContent = e.message; decideBtn.disabled = false; });
    });
    box.appendChild(el('div', {class:'btn-row'}, decideBtn));
    box.appendChild(status);
    box.appendChild(feedbackWidget(projectId, 'manager', task.id));
    return box;
  }

  function drawEvents(panel){
    if (!events.length){ panel.appendChild(emptyBox('📜', t('common.no_events_title'))); return; }
    var card = el('div', {class:'card'});
    events.slice().reverse().forEach(function(e){ card.appendChild(eventRow(e)); });
    panel.appendChild(card);
  }

  function drawUsage(panel){
    panel.appendChild(el('div', {class:'grid grid-3'},
      el('div', {class:'card stat'}, el('b', {text: fmtNum(usage.calls)}), el('span', {text:t('usage.calls')})),
      el('div', {class:'card stat'}, el('b', {text: fmtMoney(usage.cost_usd)}), el('span', {text:t('usage.cost')})),
      el('div', {class:'card stat'}, el('b', {text: fmtMoney(usage.budget_usd)}), el('span', {text:t('usage.budget')}))
    ));
    panel.appendChild(el('div', {class:'grid grid-2'},
      el('div', {class:'card stat'}, el('b', {text: fmtNum(usage.input_tokens)}), el('span', {text:t('usage.input_tokens')})),
      el('div', {class:'card stat'}, el('b', {text: fmtNum(usage.output_tokens)}), el('span', {text:t('usage.output_tokens')}))
    ));
  }

  function drawMemory(panel){
    var formCard = el('div', {class:'card stack'});
    var listBox = el('div', {class:'stack'});
    var catSel = el('select', {});
    MEMORY_CATEGORIES.forEach(function(c){ catSel.appendChild(el('option', {value:c, text:lbl('memcat', c)})); });
    var titleInput = el('input', {type:'text', placeholder:t('common.title')});
    var contentInput = el('textarea', {placeholder:t('memory.content')});
    var addStatus = el('p', {class:'err muted'});
    var addBtn = el('button', {class:'btn sm', text:t('common.add')});

    function load(){
      clear(listBox); listBox.appendChild(loadingBox());
      Promise.all([api('/projects/' + projectId + '/memory'), api('/projects/' + projectId + '/learning')])
        .then(function(r){
          clear(listBox);
          var items = r[0], traces = r[1];
          if (!items.length && !traces.length){
            listBox.appendChild(emptyBox('🧠', t('memory.empty_title'),
              t('memory.empty_sub')));
            return;
          }
          // Merge both kinds into one chronological feed — each card shows a timestamp, so the
          // list should read newest-first regardless of which endpoint it came from.
          var entries = items.map(function(m){ return {kind:'memory', at:m.created_at, data:m}; })
            .concat(traces.map(function(trace){ return {kind:'learning', at:trace.created_at, data:trace}; }));
          entries.sort(function(a, b){ return a.at < b.at ? 1 : a.at > b.at ? -1 : 0; });
          entries.forEach(function(entry){
            if (entry.kind === 'learning'){
              var trace = entry.data;
              listBox.appendChild(el('div', {class:'card stack'},
                el('div', {class:'row', style:'justify-content:space-between'},
                  el('span', {class:'chip', text:t('memory.lesson')}), el('small', {class:'muted', text: fmtTime(trace.created_at)})),
                el('b', {text:trace.concept}), el('p', {class:'muted', text:trace.explanation})));
            } else {
              var m = entry.data;
              listBox.appendChild(el('div', {class:'card stack'},
                el('div', {class:'row', style:'justify-content:space-between'},
                  el('span', {class:'chip', text: lbl('memcat', m.category)}),
                  el('small', {class:'muted', text: fmtTime(m.created_at)})),
                el('b', {text:m.title}), el('p', {class:'muted', text:m.content})));
            }
          });
        }).catch(function(e){ clear(listBox); listBox.appendChild(errorBox(e.message, load)); });
    }

    addBtn.addEventListener('click', function(){
      if (!titleInput.value.trim() || !contentInput.value.trim()){
        addStatus.textContent = t('memory.fill_title_content'); return;
      }
      addBtn.disabled = true; addStatus.textContent = '';
      api('/projects/' + projectId + '/memory', {method:'POST', json:{
        category: catSel.value, title: titleInput.value.trim(), content: contentInput.value.trim(),
      }}).then(function(){
        titleInput.value = ''; contentInput.value = ''; addBtn.disabled = false;
        load();
      }).catch(function(e){ addStatus.textContent = e.message; addBtn.disabled = false; });
    });

    formCard.appendChild(el('h3', {text:t('memory.add_title')}));
    formCard.appendChild(el('div', {class:'grid grid-2'},
      el('label', {class:'f'}, t('memory.category'), catSel), el('label', {class:'f'}, t('common.title'), titleInput)));
    formCard.appendChild(el('label', {class:'f'}, t('memory.content'), contentInput));
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
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:t('nav.agents')}),
    // A new agent starts a new interview; a refresh or language change inside one keeps its answers.
    el('button', {class:'btn sm', onclick:function(){ go('agents/new'); }}, t('agents.new_button')),
    // The technical 14-section interview, for developers (the factory's brief maps onto it in PR3).
    showCode() ? el('button', {class:'btn ghost sm', onclick:function(){ saveIntakeAnswers({}); go('agents/blueprint'); }}, t('agents.blueprint_button')) : null));
  var builds = el('div', {class:'stack'});
  root.appendChild(builds);
  api('/factory/sessions').then(function(list){
    if (!list.length) return;
    builds.appendChild(el('h3', {text: t('build.in_progress')}));
    list.forEach(function(b){
      builds.appendChild(el('button', {class:'list-item', onclick:function(){ go('build/' + b.id); }},
        el('div', {class:'top'}, el('b', {text: b.title || t('build.untitled', {id: b.id})}),
          el('span', {class:'chip', text: lbl('fstage', b.stage)})),
        el('span', {class:'muted', text: fmtTime(b.updated_at)})));
    });
  }).catch(function(){});
  var body = el('div', {class:'list'});
  root.appendChild(body);
  body.appendChild(loadingBox());
  api('/agents').then(function(agents){
    clear(body);
    if (!agents.length){ body.appendChild(emptyBox('🤖', t('agents.empty'))); return; }
    agents.forEach(function(a){
      body.appendChild(el('button', {class:'list-item', onclick:function(){ go('agents/' + encodeURIComponent(a.name)); }},
        el('div', {class:'top'}, el('b', {text:a.name}),
          el('div', {class:'row'}, el('span', {class:'chip', text: lbl('role', a.model_role)}),
            a.active ? el('span', {class:'chip', text:t('common.active')}) : el('span', {class:'chip', text:t('common.inactive')}))),
        el('span', {class:'muted', text:a.description}),
        el('div', {class:'row'}, a.capabilities.map(function(c){ return el('span', {class:'chip', text:c}); }))
      ));
    });
  }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message)); });
}

function renderAgentDetail(root, name){
  root.appendChild(el('div', {class:'page-head'},
    el('button', {class:'btn ghost sm', onclick:function(){ go('agents'); }}, t('agents.back')),
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
    tabsHolder.appendChild(tabBtn('overview', t('tab.overview')));
    tabsHolder.appendChild(tabBtn('feedback', t('agent.tab.training') + (feedbackCount !== null ? ' (' + fmtNum(feedbackCount) + ')' : '')));
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
      if (!list.length){ panel.appendChild(emptyBox('🎓', t('agent.feedback_empty_title'), t('agent.feedback_empty_sub'))); return; }
      var card = el('div', {class:'card'});
      list.forEach(function(f){
        card.appendChild(el('div', {class:'stack', style:'gap:4px;padding-block:8px;border-top:1px solid var(--line)'},
          el('div', {class:'row', style:'justify-content:space-between'},
            el('span', {class:'chip', text: f.rating === 'up' ? t('agent.feedback_up') : t('agent.feedback_down')}),
            el('time', {class:'muted', text:fmtTime(f.created_at)})),
          f.note ? el('p', {text:f.note}) : null,
          f.task_id ? el('span', {class:'muted', text: t('agent.feedback.task', {id: f.task_id})}) : null));
      });
      panel.appendChild(card);
    }).catch(function(e){ clear(panel); panel.appendChild(errorBox(e.message, function(){ drawFeedback(panel); })); });
  }

  function drawOverview(panel){
    var agent = agentData, allSkills = skillsData, body = panel;
    body.appendChild(el('div', {class:'card stack'},
      el('p', {text: agent.description}),
      el('div', {class:'row'},
        el('span', {class:'chip', text: t('agent.model_role', {role: lbl('role', agent.model_role)})}),
        agent.active ? el('span', {class:'chip', text:t('common.active')}) : el('span', {class:'chip', text:t('common.inactive')}))
    ));

    var caps = el('div', {class:'card stack'}, el('h3', {text:t('agent.capabilities')}),
      el('div', {class:'row'}, agent.capabilities.map(function(c){ return el('span', {class:'chip', text:c}); })));
    body.appendChild(caps);

    var tools = el('div', {class:'card stack'}, el('h3', {text:t('agent.tools')}),
      agent.tools.length ? el('div', {class:'row'}, agent.tools.map(function(tool){ return el('span', {class:'chip', text:tool}); }))
        : el('p', {class:'muted', text:t('agent.no_tools')}));
    body.appendChild(tools);

    var perms = el('div', {class:'card stack'}, el('h3', {text:t('agent.permissions')}));
    var permKeys = Object.keys(agent.permissions);
    if (!permKeys.length) perms.appendChild(el('p', {class:'muted', text:t('agent.no_permissions')}));
    else permKeys.forEach(function(k){
      perms.appendChild(el('div', {class:'row', style:'justify-content:space-between'},
        el('span', {text:k}), el('span', {class:'chip', text: agent.permissions[k] ? t('agent.allowed') : t('agent.denied')})));
    });
    body.appendChild(perms);

    var skillsCard = el('div', {class:'card stack'}, el('h3', {text:t('agent.skills')}));
    if (!agent.skills.length) skillsCard.appendChild(el('p', {class:'muted', text:t('agent.no_skills')}));
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
    el('button', {class:'btn ghost sm', onclick:function(){ go('agents'); }}, t('agents.back')),
    el('h2', {text:t('intake.title')})));
  var body = el('div', {class:'stack'});
  root.appendChild(body);
  // Kept in this tab's sessionStorage: a language change or a refresh re-draws the page, never loses the answers.
  var answers = loadIntakeAnswers();

  function start(){
    clear(body);
    body.appendChild(loadingBox());
    // POST, not a GET with the answers in the URL: free text can be long and URLs end up in logs.
    api('/interview/state', {method:'POST', json:{answers: answers, locale: I18N.locale()}})
      .then(draw).catch(function(e){ clear(body); body.appendChild(errorBox(e.message, start)); });
  }

  function progress(){
    var count = Object.keys(answers).length;
    if (!count) return null;
    return el('div', {class:'row', style:'justify-content:space-between'},
      el('span', {class:'muted', text: t('intake.answered', {count: fmtNum(count)})}),
      el('button', {class:'btn ghost sm', onclick: function(){ answers = {}; saveIntakeAnswers(answers); start(); }}, t('intake.restart')));
  }

  function draw(state){
    clear(body);
    var bar = progress();
    if (bar) body.appendChild(bar);
    if (state.done){ drawSummary(state); return; }
    drawQuestion(state.question);
  }

  function submit(target, value){
    clear(body);
    body.appendChild(loadingBox());
    api('/interview/answer', {method:'POST', json:{answers: answers, target: target, value: value, locale: I18N.locale()}})
      .then(function(state){ answers[target] = value; saveIntakeAnswers(answers); draw(state); })
      .catch(function(e){ clear(body); body.appendChild(errorBox(e.message, function(){ submit(target, value); })); });
  }

  function drawQuestion(q){
    var card = el('div', {class:'card stack'}, el('h3', {text:q.text}));
    if (q.type === 'single_choice' || q.type === 'confirm'){
      var opts = q.type === 'confirm' ? [{value:true,label:t('common.yes')},{value:false,label:t('common.no')}] : q.options;
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
      card.appendChild(el('button', {class:'btn sm', style:'margin-top:8px', onclick:function(){ submit(q.target, chosen); }}, t('common.continue')));
    } else {
      var input = el('input', {type:'text'});
      card.appendChild(input);
      card.appendChild(el('button', {class:'btn sm', style:'margin-top:8px', onclick:function(){ submit(q.target, input.value); }}, t('common.continue')));
    }
    if (q.note) card.appendChild(el('p', {class:'muted', text:q.note}));
    body.appendChild(card);
  }

  function drawSummary(state){
    var bp = state.blueprint;
    var card = el('div', {class:'card stack'}, el('h3', {text:t('intake.summary_title')}));
    Object.keys(bp).forEach(function(section){
      card.appendChild(el('div', {class:'row', style:'justify-content:space-between'},
        el('b', {text:section}), el('span', {class:'muted', text: showCode() ? JSON.stringify(bp[section]) : readable(bp[section])})));
    });
    if (state.issues && state.issues.length){
      card.appendChild(el('h3', {text:t('intake.issues')}));
      state.issues.forEach(function(i){ card.appendChild(el('p', {class:'muted', text:'⚠️ ' + i})); });
    } else {
      card.appendChild(el('p', {text:t('intake.valid')}));
    }
    card.appendChild(el('p', {text:t('intake.confirm_path')}));
    body.appendChild(card);
  }

  start();
}

function loadIntakeAnswers(){
  try { var v = JSON.parse(sessionStorage.getItem('af_intake_answers') || '{}'); return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; }
  catch(e){ return {}; }
}
function saveIntakeAnswers(answers){
  try { sessionStorage.setItem('af_intake_answers', JSON.stringify(answers)); } catch(e){}
}
// A blueprint section as plain "field: value" lines (no JSON) for owners who don't want to see code.
function readable(value){
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? t('common.yes') : t('common.no');
  if (Array.isArray(value)) return value.length ? value.map(readable).join(t('list.sep')) : '—';
  if (typeof value === 'object') return Object.keys(value).map(function(k){ return k + ': ' + readable(value[k]); }).join(' · ');
  return String(value);
}


/* ============================== building an agent (app/factory.py) ============================== */

var BUILD_STEPS = [
  {key:'understand', stages:['DRAFT','CLARIFYING']},
  {key:'brief', stages:['AWAITING_BRIEF_APPROVAL']},
  {key:'design', stages:['DESIGNING','AWAITING_DESIGN_APPROVAL']},
  {key:'build', stages:['BUILDING']},
  {key:'evaluate', stages:['EVALUATING','AWAITING_ACTIVATION_APPROVAL']},
  {key:'active', stages:['ACTIVE']}
];

function newRequestId(){
  try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, ''); } catch(e){}
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
}

// "+ New agent": one build per click. The request id survives a refresh of this route, so it never makes two.
function startBuild(root){
  var rid = null;
  try { rid = sessionStorage.getItem('af_new_build'); } catch(e){}
  if (!rid){ rid = newRequestId(); try { sessionStorage.setItem('af_new_build', rid); } catch(e){} }
  root.appendChild(loadingBox());
  api('/factory/sessions?locale=' + encodeURIComponent(I18N.locale()), {method:'POST', json:{request_id: rid}})
    .then(function(st){
      try { sessionStorage.removeItem('af_new_build'); } catch(e){}
      location.replace('#build/' + st.session.id);
    })
    .catch(function(e){ clear(root); root.appendChild(errorBox(e.message, function(){ clear(root); startBuild(root); })); });
}

function renderBuild(root, id){
  root.appendChild(el('div', {class:'page-head'},
    el('button', {class:'btn ghost sm', onclick:function(){ go('agents'); }}, t('agents.back')),
    el('h2', {id:'build-title', text: t('build.title')})));
  var body = el('div', {class:'stack'});
  root.appendChild(body);
  var st = null;

  function load(){
    clear(body); body.appendChild(loadingBox());
    api('/factory/sessions/' + id + '?locale=' + encodeURIComponent(I18N.locale())).then(draw)
      .catch(function(e){ clear(body); body.appendChild(errorBox(e.message, load)); });
  }
  // Every change names the revision it was made against and has its own request id (app/factory.py): another tab's
  // change in between is a 409 that reloads, a double click applies once.
  function send(path, payload, status){
    payload.expected_revision = st.session.revision;
    payload.request_id = newRequestId();
    return api('/factory/sessions/' + id + path + '?locale=' + encodeURIComponent(I18N.locale()), {method:'POST', json: payload})
      .then(draw)
      .catch(function(e){
        if (e.code === 'stale_revision' || e.code === 'stale_version'){ notify(t('build.stale')); load(); return; }
        if (status) status.textContent = e.message; else notify(e.message);
      });
  }

  function stepBar(stage){
    var bar = el('ol', {class:'stepbar', 'aria-label': t('build.steps')});
    var reached = true;
    BUILD_STEPS.forEach(function(step){
      var here = step.stages.indexOf(stage) >= 0;
      var cls = here ? 'step now' : (reached ? 'step done' : 'step');
      bar.appendChild(el('li', {class: cls, 'aria-current': here ? 'step' : null},
        el('span', {class:'step-mark', 'aria-hidden':'true', text: here ? '●' : (reached ? '✓' : '○')}),
        el('span', {text: t('build.step.' + step.key)})));
      if (here) reached = false;
    });
    return bar;
  }

  function questionCard(q){
    var card = el('div', {class:'card stack question-card'});
    if (q.kind === 'clarify') card.appendChild(el('p', {class:'chip', text: t('build.clarify')}));
    if (q.kind === 'conflict') card.appendChild(el('p', {class:'chip risk-medium', text: t('build.conflict.' + q.conflict)}));
    card.appendChild(el('h3', {text: q.text}));
    (q.help || []).forEach(function(h){ card.appendChild(el('p', {class:'muted', text: h})); });
    if (q.field_key) card.appendChild(el('code', {dir:'ltr', text: 'brief.' + q.field_key}));
    var input = el('textarea', {id:'build-answer', rows:'3', 'aria-label': q.text});
    card.appendChild(input);
    var status = el('p', {class:'err muted', role:'status'});
    var next = el('button', {class:'btn', text: t('common.continue')});
    next.addEventListener('click', function(){
      if (!input.value.trim()){ status.textContent = t('build.answer_needed'); return; }
      next.disabled = true;
      send('/answer', {field: q.field, value: input.value}, status).then(function(){ next.disabled = false; });
    });
    var row = el('div', {class:'btn-row'}, next);
    if (q.can_suggest){
      var idk = el('button', {class:'btn ghost', text: t('build.dont_know')});
      idk.addEventListener('click', function(){ idk.disabled = true; send('/answer', {field: q.field, dont_know: true}, status); });
      row.appendChild(idk);
      if (q.suggestion) card.appendChild(el('p', {class:'muted', text: t('build.suggestion_is', {value: q.suggestion})}));
    }
    card.appendChild(row);
    card.appendChild(status);
    setTimeout(function(){ try { input.focus(); } catch(e){} }, 0);
    return card;
  }

  function fieldRow(f, editable){
    var wrap = el('div', {class:'brief-row stack'});
    var valueText = f.value === null || f.value === undefined ? t('build.open_question') : String(f.value);
    wrap.appendChild(el('div', {class:'row', style:'justify-content:space-between'},
      el('b', {text: lbl('bfield', f.field)}),
      el('span', {class:'row'},
        el('span', {class:'chip status-' + f.status, text: lbl('fstatus', f.status)}),
        el('span', {class:'chip', text: lbl('fsource', f.source)}))));
    var value = el('p', {dir:'auto', text: valueText});
    wrap.appendChild(value);
    if (editable){
      var edit = el('button', {class:'btn ghost sm', text: t('build.edit')});
      edit.addEventListener('click', function(){
        clear(wrap);
        var input = el('textarea', {rows:'2', 'aria-label': lbl('bfield', f.field)});
        input.value = f.value || '';
        var status = el('p', {class:'err muted', role:'status'});
        var save = el('button', {class:'btn sm', text: t('common.save')});
        save.addEventListener('click', function(){
          if (!input.value.trim()){ status.textContent = t('build.answer_needed'); return; }
          send('/answer', {field: f.field, value: input.value}, status);
        });
        wrap.appendChild(el('b', {text: lbl('bfield', f.field)}));
        wrap.appendChild(input);
        wrap.appendChild(el('div', {class:'btn-row'}, save,
          el('button', {class:'btn ghost sm', onclick: function(){ draw(st); }}, t('build.cancel_edit'))));
        wrap.appendChild(status);
      });
      wrap.appendChild(edit);
    }
    return wrap;
  }

  function summaryCard(){
    var editable = ['DRAFT','CLARIFYING','AWAITING_BRIEF_APPROVAL','DESIGNING'].indexOf(st.session.stage) >= 0;
    var card = el('div', {class:'card stack'}, el('h3', {text: t('build.summary_title')}));
    [['from_you', t('build.from_you')], ['proposals', t('build.proposals')], ['unknowns', t('build.unknowns')]].forEach(function(sec){
      var rows = st.summary[sec[0]];
      if (!rows.length) return;
      card.appendChild(el('h4', {text: sec[1]}));
      rows.forEach(function(f){ card.appendChild(fieldRow(f, editable)); });
    });
    if (st.summary.missing.length){
      card.appendChild(el('p', {class:'err muted', text: t('build.missing', {fields: st.summary.missing.map(function(f){ return lbl('bfield', f); }).join(t('list.sep'))})}));
    }
    if (showCode()){
      card.appendChild(el('details', {class:'advanced'}, el('summary', {text: 'brief v' + st.brief.version + ' (JSON)'}),
        el('pre', {class:'code', dir:'ltr', text: JSON.stringify(st.brief.fields, null, 2)})));
    }
    return card;
  }

  function actions(){
    var stage = st.session.stage;
    var status = el('p', {class:'err muted', role:'status'});
    var row = el('div', {class:'btn-row'});
    if (stage === 'AWAITING_BRIEF_APPROVAL'){
      var ok = el('button', {class:'btn', text: t('build.approve'), disabled: st.can_approve ? null : 'disabled'});
      ok.addEventListener('click', function(){ ok.disabled = true; send('/approve', {brief_version: st.brief.version}, status); });
      row.appendChild(ok);
    }
    if (stage === 'AWAITING_BRIEF_APPROVAL' || stage === 'DESIGNING'){
      row.appendChild(el('button', {class:'btn ghost', onclick: function(){ send('/request-changes', {}, status); }}, t('build.request_changes')));
    }
    if (stage === 'PAUSED') row.appendChild(el('button', {class:'btn', onclick: function(){ send('/pause', {paused: false}, status); }}, t('build.resume')));
    else if (stage !== 'CANCELLED' && stage !== 'ACTIVE'){
      row.appendChild(el('button', {class:'btn ghost', onclick: function(){ send('/pause', {paused: true}, status); }}, t('build.pause')));
      row.appendChild(el('button', {class:'btn ghost', onclick: function(){
        if (window.confirm(t('build.cancel_confirm'))) send('/cancel', {}, status);
      }}, t('build.cancel')));
    }
    return el('div', {class:'card stack'}, row, status);
  }


  // ---------- build, evaluation, activation (app/build.py) ----------
  function buildSection(b){
    var stage = st.session.stage;
    var card = el('div', {class:'card stack build-card'}, el('h3', {text: t('agentbuild.title')}));
    var status = el('p', {class:'err muted', role:'status'});
    var c = b.candidate;
    if (stage === 'BUILDING'){
      var next = c ? c.version + 1 : 1;
      card.appendChild(el('p', {class:'muted', text: t('agentbuild.build_help', {version: fmtNum(next)})}));
      var mk = el('button', {class:'btn', text: t('agentbuild.build')});
      mk.addEventListener('click', function(){ mk.disabled = true; send('/build', {}, status); });
      card.appendChild(el('div', {class:'btn-row'}, mk));
    }
    if (c){
      card.appendChild(el('p', {}, el('b', {text: t('agentbuild.candidate', {version: fmtNum(c.version), agent: c.agent}) + ' · '}),
        el('span', {class:'chip vstate-' + c.state, text: lbl('vstate', c.state)})));
      card.appendChild(el('p', {class:'muted', text: c.spec.description}));
      card.appendChild(el('details', {class:'advanced'}, el('summary', {text: t('agentbuild.instructions')}),
        el('pre', {class:'code', dir:'ltr', text: c.spec.instructions})));
    }
    if (stage === 'EVALUATING' && c && c.state === 'built'){
      card.appendChild(el('p', {class:'muted', text: t('agentbuild.evaluate_help', {used: fmtNum(b.rounds_used + 1), max: fmtNum(b.max_rounds)})}));
      var ev = el('button', {class:'btn', text: t('agentbuild.evaluate')});
      ev.addEventListener('click', function(){ ev.disabled = true; ev.textContent = t('agentbuild.evaluating'); send('/evaluate', {}, status); });
      card.appendChild(el('div', {class:'btn-row'}, ev));
    }
    b.evaluations.slice(0, 1).forEach(function(e){
      var box = el('div', {class:'stack'}, el('b', {text: t('agentbuild.round', {round: fmtNum(e.round), version: fmtNum(e.agent_version), verdict: lbl('verdict', e.verdict)})}));
      e.cases.forEach(function(k){
        var ok = k.verdict === 'PASS';
        var reply = typeof k.output === 'string' ? k.output : (k.output && k.output.summary) || '';
        box.appendChild(el('div', {class:'case ' + (ok ? 'case-pass' : 'case-fail')},
          el('p', {}, el('b', {text: (ok ? '✓ ' : '✗ ') + lbl('dtest', k.kind) + ': '}), k.input || '—'),
          el('p', {class:'muted', text: t('agentbuild.reply') + ': ' + reply}),
          el('p', {class:'muted', text: k.reason === 'invalid_output' ? t('agentbuild.invalid_output') : (k.reason === 'judge_invalid' ? t('agentbuild.judge_invalid') : k.reason)})));
      });
      if (e.verdict === 'changes_required' && stage !== 'EVALUATING') box.appendChild(el('p', {class:'notice', text: t('agentbuild.failed_help')}));
      card.appendChild(box);
    });
    if (stage === 'AWAITING_ACTIVATION_APPROVAL' && c){
      card.appendChild(el('p', {class:'muted', text: t('agentbuild.activate_help')}));
      var on = el('button', {class:'btn', text: t('agentbuild.activate', {version: fmtNum(c.version)})});
      on.addEventListener('click', function(){ on.disabled = true; send('/activate', {agent_version: c.version}, status); });
      card.appendChild(el('div', {class:'btn-row'}, on));
    }
    var active = (b.history || []).filter(function(v){ return v.state === 'active'; })[0];
    if (active) card.appendChild(el('p', {class:'chip status-COMPLETED', text: t('agentbuild.active', {agent: active.agent, version: fmtNum(active.version)})}));
    if ((b.history || []).length > 1 || (active && stage === 'ACTIVE')){
      var list = el('div', {class:'stack', style:'gap:6px'}, el('b', {text: t('agentbuild.history')}));
      b.history.forEach(function(v){
        var row = el('div', {class:'row', style:'justify-content:space-between'},
          el('span', {text: 'v' + v.version + ' · ' + lbl('vstate', v.state)}));
        if (v.state === 'retired'){
          var back = el('button', {class:'btn ghost sm', text: t('agentbuild.rollback', {version: fmtNum(v.version)})});
          back.addEventListener('click', function(){
            if (!window.confirm(t('agentbuild.rollback_confirm', {version: fmtNum(v.version)}))) return;
            back.disabled = true;
            api('/factory/agents/' + encodeURIComponent(v.agent) + '/rollback', {method:'POST', json:{to_version: v.version, request_id: newRequestId()}})
              .then(load).catch(function(err){ back.disabled = false; status.textContent = err.message; });
          });
          row.appendChild(back);
        }
        list.appendChild(row);
      });
      card.appendChild(list);
    }
    card.appendChild(status);
    return card;
  }


  // ---------- the design canvas (app/design.py) ----------
  var selectedBranch = null;
  var view = {x: 0, y: 0, w: 1000, h: 700};

  function fmtValue(v){
    if (v === null || v === undefined || v === '') return t('build.open_question');
    if (typeof v === 'boolean') return v ? t('common.yes') : t('common.no');
    if (Array.isArray(v)){
      if (!v.length) return '—';
      if (typeof v[0] === 'object') return v.map(function(c){ return lbl('dtest', c.kind) + ': ' + (c.input || '—') + ' → ' + (c.expect || '—'); }).join(' | ');
      return v.join(t('list.sep'));
    }
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }
  function statusIcon(status){ return status === 'confirmed' ? '✓' : (status === 'incomplete' ? '⚠' : '◌'); }

  function designActions(d){
    var status = el('p', {class:'err muted', role:'status'});
    var row = el('div', {class:'btn-row'});
    var assist = el('button', {class:'btn ghost', text: t('design.assist')});
    assist.addEventListener('click', function(){ assist.disabled = true; send('/design/assist', {}, status).then(function(){ assist.disabled = false; }); });
    row.appendChild(assist);
    var older = (d.history || []).filter(function(h){ return h.version < d.version; })[0];
    if (older){
      row.appendChild(el('button', {class:'btn ghost', onclick: function(){ send('/design/revert', {to_version: older.version}, status); }},
        t('design.undo', {version: fmtNum(older.version)})));
    }
    if (st.session.stage === 'AWAITING_DESIGN_APPROVAL'){
      var ok = el('button', {class:'btn', text: t('design.approve'), disabled: d.can_approve ? null : 'disabled'});
      ok.addEventListener('click', function(){ ok.disabled = true; send('/design/approve', {blueprint_version: d.version}, status); });
      row.appendChild(ok);
    }
    var b = d.budget;
    return el('div', {class:'card stack'},
      el('p', {class:'muted', text: t('design.version', {version: fmtNum(d.version), brief: fmtNum(d.brief_version)})}),
      el('p', {class:'muted', text: t('design.budget', {spent: fmtMoney(b.spent_usd), limit: fmtMoney(b.limit_usd)})}),
      row, status);
  }

  function canvas(d){
    var NS = 'http://www.w3.org/2000/svg';
    function svgEl(tag, attrs, text){
      var n = document.createElementNS(NS, tag);
      for (var k in attrs) n.setAttribute(k, attrs[k]);
      if (text !== undefined) n.textContent = text;
      return n;
    }
    var svg = svgEl('svg', {class:'canvas', role:'group', 'aria-label': t('design.canvas_label'),
      viewBox: [view.x, view.y, view.w, view.h].join(' ')});
    var cx = 500, cy = 350, r = 265, n = d.branches.length;
    var edges = svgEl('g', {}), nodes = svgEl('g', {});
    d.branches.forEach(function(b, i){
      var a = -Math.PI / 2 + i * 2 * Math.PI / n;
      var x = cx + r * Math.cos(a) * 1.35, y = cy + r * Math.sin(a);
      // A soft curve (organic branches), bending a little sideways.
      var mx = (cx + x) / 2 + 40 * Math.sin(a), my = (cy + y) / 2 - 40 * Math.cos(a);
      edges.appendChild(svgEl('path', {class:'edge ' + b.status, d: 'M' + cx + ',' + cy + ' Q' + mx + ',' + my + ' ' + x + ',' + y}));
      var g = svgEl('g', {class:'node ' + b.status + (selectedBranch === b.key ? ' selected' : ''), tabindex:'0', role:'button',
        'aria-label': lbl('branch', b.key) + ' — ' + lbl('dstatus', b.status), transform: 'translate(' + (x - 95) + ',' + (y - 34) + ')'});
      g.appendChild(svgEl('rect', {width:'190', height:'68', rx:'14'}));
      g.appendChild(svgEl('text', {x:'95', y:'30', 'text-anchor':'middle'}, lbl('branch', b.key)));
      g.appendChild(svgEl('text', {x:'95', y:'52', 'text-anchor':'middle', class:'small'}, statusIcon(b.status) + ' ' + lbl('dstatus', b.status)));
      function open(){ selectedBranch = b.key; draw(st); }
      g.addEventListener('click', open);
      g.addEventListener('keydown', function(e){ if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); open(); } });
      nodes.appendChild(g);
    });
    svg.appendChild(edges);
    var center = svgEl('g', {class:'center'});
    center.appendChild(svgEl('rect', {x: cx - 130, y: cy - 40, width:'260', height:'80', rx:'40'}));
    var title = (st.session.title || d.blueprint.agent_core.description || '').slice(0, 28);
    center.appendChild(svgEl('text', {x: cx, y: cy + 6, 'text-anchor':'middle'}, title || t('build.title')));
    svg.appendChild(center);
    svg.appendChild(nodes);

    // Zoom, pan, fit: only the view moves; nothing is changed or run (FACTORY_UI_SPEC §3.2).
    function setView(){ svg.setAttribute('viewBox', [view.x, view.y, view.w, view.h].join(' ')); }
    function zoom(f){
      var nw = Math.min(2000, Math.max(400, view.w * f)), nh = nw * 0.7;
      view.x += (view.w - nw) / 2; view.y += (view.h - nh) / 2; view.w = nw; view.h = nh; setView();
    }
    svg.addEventListener('wheel', function(e){ e.preventDefault(); zoom(e.deltaY > 0 ? 1.1 : 0.9); }, {passive: false});
    var drag = null;
    svg.addEventListener('pointerdown', function(e){ if (e.target === svg){ drag = {x: e.clientX, y: e.clientY}; svg.setPointerCapture(e.pointerId); } });
    svg.addEventListener('pointermove', function(e){
      if (!drag) return;
      var k = view.w / svg.getBoundingClientRect().width;
      view.x -= (e.clientX - drag.x) * k * (document.documentElement.dir === 'rtl' ? 1 : 1);
      view.y -= (e.clientY - drag.y) * k;
      drag = {x: e.clientX, y: e.clientY}; setView();
    });
    svg.addEventListener('pointerup', function(){ drag = null; });
    var tools = el('div', {class:'canvas-tools'},
      el('button', {type:'button', 'aria-label': t('design.zoom_in'), onclick: function(){ zoom(0.85); }}, '+'),
      el('button', {type:'button', 'aria-label': t('design.zoom_out'), onclick: function(){ zoom(1.18); }}, '−'),
      el('button', {type:'button', 'aria-label': t('design.fit'), onclick: function(){ view = {x:0, y:0, w:1000, h:700}; setView(); }}, '⤢'));
    var card = el('div', {class:'card canvas-card'}, tools);
    card.appendChild(svg);
    return card;
  }

  function editor(b, d){
    var form = el('div', {class:'stack'});
    var inputs = {};
    var level = audience();
    Object.keys(b.editable).forEach(function(path){
      var kind = b.editable[path];
      if (kind === 'name' && level !== 'developer') return;  // the slug is a developer's detail
      if (kind === 'role' && level === 'ordinary') return;   // the system's choice, shown with its reason
      var v = b.values[path], input;
      if (kind === 'bool'){
        input = el('input', {type:'checkbox'}); input.checked = !!v;
      } else if (kind === 'money'){
        input = el('input', {type:'number', min:'0.01', step:'0.01', dir:'ltr'}); input.value = v;
      } else if (kind === 'role' || kind === 'gate' || kind === 'classification'){
        input = el('select', {});
        var options = kind === 'role' ? MODEL_ROLES : (kind === 'gate' ? d.gates_allowed : ['public','internal','sensitive']);
        options.forEach(function(o){ input.appendChild(el('option', {value:o}, lbl(kind === 'role' ? 'role' : 'dchoice', o))); });
        input.value = v;
      } else if (kind === 'sources' || kind === 'tools'){
        input = el('div', {class:'row', role:'group'});
        (kind === 'sources' ? ['task.input','memory.phase_current','skills'] : ['web_search','repo_read','repo_write','terminal','deploy']).forEach(function(o){
          var cb = el('input', {type:'checkbox', value:o}); cb.checked = (v || []).indexOf(o) >= 0;
          input.appendChild(el('label', {class:'row'}, cb, lbl('dchoice', o)));
        });
      } else {
        input = el('textarea', {rows: kind === 'text' ? '3' : '2', dir:'auto'});
        input.value = Array.isArray(v) ? v.join(t('list.sep')) : (v || '');
      }
      inputs[path] = {kind: kind, input: input};
      form.appendChild(el('label', {class:'f'}, lbl('dfield', path), input));
    });
    if (!Object.keys(inputs).length) return null;
    var status = el('p', {class:'err muted', role:'status'});
    var save = el('button', {class:'btn sm', text: t('common.save')});
    save.addEventListener('click', function(){
      var changes = {};
      Object.keys(inputs).forEach(function(path){
        var it = inputs[path], value;
        if (it.kind === 'bool') value = it.input.checked;
        else if (it.kind === 'money') value = Number(it.input.value);
        else if (it.kind === 'sources' || it.kind === 'tools') value = Array.prototype.filter.call(it.input.querySelectorAll('input'), function(c){ return c.checked; }).map(function(c){ return c.value; });
        else value = it.input.value;
        if (JSON.stringify(value) !== JSON.stringify(b.values[path])) changes[path] = value;
      });
      if (!Object.keys(changes).length){ status.textContent = t('design.nothing_changed'); return; }
      send('/design/edit', {branch: b.key, changes: changes}, status);
    });
    form.appendChild(el('div', {class:'btn-row'}, save));
    form.appendChild(status);
    return el('details', {class:'advanced'}, el('summary', {text: t('build.edit')}), form);
  }

  function branchPanel(b, d){
    var panel = el('div', {class:'card stack branch-panel', 'aria-live':'polite'});
    panel.appendChild(el('h3', {}, statusIcon(b.status) + ' ' + lbl('branch', b.key)));
    panel.appendChild(el('p', {class:'chip dstatus', text: lbl('dstatus', b.status) + ' · ' + lbl('dsource', b.source)}));
    panel.appendChild(el('p', {class:'muted', text: t('branch_help.' + b.key)}));
    Object.keys(b.values).forEach(function(path){
      if (path === 'agent_core.name' && audience() !== 'developer') return;  // a developer's detail
      panel.appendChild(el('div', {class:'brief-row'}, el('b', {text: lbl('dfield', path)}), el('p', {dir:'auto', text: fmtValue(b.values[path])})));
    });
    b.issues.forEach(function(i){ panel.appendChild(el('p', {class:'err muted', dir:'ltr', text: '⚠ ' + i})); });
    if (showCode()) panel.appendChild(el('p', {class:'muted', dir:'ltr', text: t('design.sections') + ': ' + b.sections.join(', ')}));
    var ed = editor(b, d);
    if (ed) panel.appendChild(ed);
    var status = el('p', {class:'err muted', role:'status'});
    if (b.status !== 'confirmed'){
      var ok = el('button', {class:'btn sm', text: t('design.confirm_branch'), disabled: b.status === 'incomplete' ? 'disabled' : null});
      ok.addEventListener('click', function(){
        ok.disabled = true;
        selectedBranch = null;  // after a confirmation the next unconfirmed branch opens (progressive, one at a time)
        send('/design/confirm', {branch: b.key, blueprint_version: d.version}, status);
      });
      panel.appendChild(el('div', {class:'btn-row'}, ok));
    }
    panel.appendChild(status);
    return panel;
  }

  function designSection(d){
    var wrap = el('div', {class:'stack'});
    wrap.appendChild(el('h3', {text: t('design.title')}));
    if (!selectedBranch){
      var next = d.branches.filter(function(b){ return b.status !== 'confirmed'; })[0];
      selectedBranch = next ? next.key : d.branches[0].key;
    }
    var sel = d.branches.filter(function(b){ return b.key === selectedBranch; })[0] || d.branches[0];
    var narrow = window.matchMedia && window.matchMedia('(max-width: 899px)').matches;
    if (narrow){
      // Phones: the branches as a step list, the selected one opened in place; no dragging needed.
      var list = el('div', {class:'list branch-list'});
      d.branches.forEach(function(b){
        list.appendChild(el('button', {class:'list-item', 'aria-expanded': String(b.key === sel.key), onclick: function(){ selectedBranch = b.key; draw(st); }},
          el('div', {class:'top'}, el('b', {text: statusIcon(b.status) + ' ' + lbl('branch', b.key)}), el('span', {class:'chip', text: lbl('dstatus', b.status)}))));
        if (b.key === sel.key) list.appendChild(branchPanel(b, d));
      });
      wrap.appendChild(list);
    } else {
      wrap.appendChild(el('div', {class:'design-layout'}, canvas(d), branchPanel(sel, d)));
    }
    wrap.appendChild(designActions(d));
    if (showCode()){
      wrap.appendChild(el('details', {class:'advanced'}, el('summary', {text: 'blueprint v' + d.version + ' (JSON)'}),
        el('pre', {class:'code', dir:'ltr', text: JSON.stringify(d.blueprint, null, 2)})));
    }
    return wrap;
  }

  function draw(next){
    st = next;
    clear(body);
    var title = document.getElementById('build-title');
    if (title) title.textContent = st.session.title || t('build.title');
    body.appendChild(stepBar(st.session.stage === 'PAUSED' ? null : st.session.stage));
    body.appendChild(el('p', {class:'muted', text: t('build.policy', {policy: lbl('policy', st.session.interaction_policy)})}));
    if (st.session.stage === 'PAUSED') body.appendChild(el('p', {class:'notice', role:'status', text: t('build.paused_banner')}));
    if (st.session.stage === 'CANCELLED') body.appendChild(el('p', {class:'notice', role:'status', text: t('build.cancelled_banner')}));
    if (st.approval) body.appendChild(el('p', {class:'chip status-COMPLETED', text: t('build.approved', {version: fmtNum(st.approval.version), time: fmtTime(st.approval.decided_at)})}));
    if (st.session.stage === 'BUILDING') body.appendChild(el('p', {class:'muted', text: t('design.next_build')}));
    if (st.question){
      body.appendChild(el('p', {class:'muted', text: t('build.progress', {answered: fmtNum(st.progress.answered), total: fmtNum(st.progress.total)})}));
      body.appendChild(questionCard(st.question));
    }
    if (st.design) body.appendChild(designSection(st.design));
    if (st.build) body.appendChild(buildSection(st.build));
    body.appendChild(summaryCard());
    body.appendChild(actions());
  }

  load();
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
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:t('nav.settings')})));
  root.appendChild(accountCard());
  root.appendChild(preferencesCard());
  var scopePicker = el('div', {class:'tabs', role:'tablist'});
  var scopeExtra = el('div', {class:'card', id:'scope-extra'});
  var body = el('div', {class:'stack'});
  root.appendChild(scopePicker);
  root.appendChild(scopeExtra);
  root.appendChild(body);

  var scope = 'global';
  var scopeId = 0;
  var scopeLabels = {global:t('settings.scope.global'), workspace:t('common.workspace'), project:t('common.project'), task:t('common.task')};

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
        if (!ws.length){ scopeExtra.appendChild(el('p', {class:'muted', text:t('settings.no_workspaces')})); body.appendChild(el('p')); return; }
        var sel = el('select', {}); ws.forEach(function(w){ sel.appendChild(el('option', {value:w.id}, w.name)); });
        sel.addEventListener('change', function(){ scopeId = Number(sel.value); loadLayer(); });
        scopeExtra.appendChild(el('label', {class:'f'}, t('common.workspace'), sel));
        scopeId = Number(sel.value); loadLayer();
      });
      return;
    }
    if (scope === 'project'){
      var loading2 = loadingBox();
      scopeExtra.appendChild(loading2);
      fetchProjects().then(function(ps){
        clear(scopeExtra);
        if (!ps.length){ scopeExtra.appendChild(el('p', {class:'muted', text:t('common.no_projects')})); return; }
        var sel = el('select', {}); ps.forEach(function(p){ sel.appendChild(el('option', {value:p.id}, p.title)); });
        sel.addEventListener('change', function(){ scopeId = Number(sel.value); loadLayer(); });
        scopeExtra.appendChild(el('label', {class:'f'}, t('common.project'), sel));
        scopeId = Number(sel.value); loadLayer();
      });
      return;
    }
    if (scope === 'task'){
      var loading3 = loadingBox();
      scopeExtra.appendChild(loading3);
      fetchProjects().then(function(ps){
        clear(scopeExtra);
        if (!ps.length){ scopeExtra.appendChild(el('p', {class:'muted', text:t('common.no_projects')})); return; }
        var psel = el('select', {}); ps.forEach(function(p){ psel.appendChild(el('option', {value:p.id}, p.title)); });
        var tsel = el('select', {});
        scopeExtra.appendChild(el('label', {class:'f'}, t('common.project'), psel));
        scopeExtra.appendChild(el('label', {class:'f'}, t('common.task'), tsel));
        function loadTasks(){
          clear(tsel); clear(body);
          api('/projects/' + psel.value + '/tasks').then(function(ts){
            if (!ts.length){
              tsel.appendChild(el('option', {value:''}, t('settings.no_tasks_option')));
              body.appendChild(el('p', {class:'muted', text:t('settings.project_has_no_tasks')}));
              return;
            }
            ts.forEach(function(task){ tsel.appendChild(el('option', {value:task.id}, task.title)); });
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
      var input = el('input', {type:'number', step: step || 'any', placeholder: t('settings.inherits', {value: getPath(resolved, path)})});
      if (cur !== undefined) input.value = cur;
      input.addEventListener('input', function(){
        if (input.value === '') { unsetPath(overrides, path); }
        else setPath(overrides, path, Number(input.value));
      });
      return el('label', {class:'f'}, label, input);
    }
    function optLabel(group, v){ return group ? lbl(group, v) : (v === null || v === undefined || v === '' ? '—' : String(v)); }
    function selectField(path, label, options, group){
      var cur = getPath(overrides, path);
      var sel = el('select', {});
      sel.appendChild(el('option', {value:''}, t('settings.inherits_option', {value: optLabel(group, getPath(resolved, path))})));
      options.forEach(function(o){ sel.appendChild(el('option', {value:o}, optLabel(group, o))); });
      sel.value = cur !== undefined ? cur : '';
      sel.addEventListener('change', function(){
        if (sel.value === '') unsetPath(overrides, path);
        else setPath(overrides, path, sel.value);
      });
      return el('label', {class:'f'}, label, sel);
    }
    function textField(path, label){
      var cur = getPath(overrides, path);
      var input = el('input', {type:'text', placeholder: t('settings.inherits', {value: getPath(resolved, path) || ''})});
      if (cur !== undefined) input.value = cur;
      input.addEventListener('input', function(){
        if (input.value.trim() === '') unsetPath(overrides, path);
        else setPath(overrides, path, input.value.trim());
      });
      return el('label', {class:'f'}, label, input);
    }

    var general = el('fieldset', {}, el('legend', {text:t('settings.general')}),
      selectField('mode', t('common.mode'), ['automatic','manual_learning'], 'mode'),
      selectField('depth', t('settings.depth'), ['quick','standard','deep'], 'depth'),
      numberField('max_steps', t('settings.max_steps'), '1'));

    var behaviour = el('fieldset', {}, el('legend', {text:t('settings.behaviour')}),
      el('p', {class:'muted', text: t('settings.policy_note')}),
      el('p', {class:'chip', text: t('settings.effective_policy', {policy: lbl('policy', resolved.interaction_policy)})}),
      selectField('interaction_policy', t('settings.interaction_policy'), INTERACTION_POLICIES, 'policy'),
      selectField('max_interaction_policy', t('settings.max_interaction_policy'), INTERACTION_POLICIES, 'policy'),
      selectField('quality_target', t('settings.quality_target'), QUALITY_TARGETS, 'quality'),
      selectField('output_locale', t('settings.output_locale'), OUTPUT_LOCALES, 'outloc'));

    var models = el('fieldset', {}, el('legend', {text:t('settings.models')}));
    MODEL_ROLES.forEach(function(role){
      models.appendChild(el('div', {class:'card', style:'background:var(--surface-2)'},
        el('p', {class:'muted', text: lbl('role', role)}),
        el('div', {class:'grid grid-3'},
          selectField('models.' + role + '.provider', t('common.provider'), PROVIDERS, null),
          textField('models.' + role + '.model', t('common.model')),
          selectField('models.' + role + '.effort', t('settings.effort'), ['low','medium','high'], 'effort'))));
    });
    if (resolved.model_access !== undefined){
      models.appendChild(el('div', {class:'card', style:'background:var(--surface-2)'},
        selectField('model_access', t('settings.model_access'), ['api_key','claude_account','ollama'], 'access')));
    }

    var budget = el('fieldset', {}, el('legend', {text:t('settings.budget_limits')}),
      numberField('budget.project_usd', t('settings.project_budget'), '0.01'),
      numberField('budget.task_usd', t('settings.task_budget'), '0.01'),
      numberField('budget.max_output_tokens', t('settings.max_output_tokens'), '1'),
      numberField('budget.max_web_searches', t('settings.max_web_searches'), '1'),
      numberField('context.max_chars', t('settings.context_chars'), '1'));

    var approvals = el('fieldset', {}, el('legend', {text:t('settings.approvals')}),
      selectField('approvals.low', t('settings.risk_low'), ['auto','manager','user'], 'approval'),
      selectField('approvals.medium', t('settings.risk_medium'), ['auto','manager','user'], 'approval'),
      selectField('approvals.high', t('settings.risk_high'), ['auto','manager','user'], 'approval'));

    var saveBtn = el('button', {class:'btn', text:t('common.save')});
    saveBtn.addEventListener('click', function(){
      saveBtn.disabled = true; status.textContent = '';
      api('/settings/' + scope + '/' + scopeId, {method:'PUT', json: overrides})
        .then(function(){ status.className = 'muted'; status.textContent = t('common.saved'); loadLayer(); })
        .catch(function(e){ status.className = 'err muted'; status.textContent = e.message; saveBtn.disabled = false; });
    });

    // An ordinary owner sees a short form; model routing opens on demand. The level hides nothing it may change.
    var advanced = el('details', {class:'advanced'}, el('summary', {text: t('settings.advanced')}), models);
    if (audience() !== 'ordinary') advanced.open = true;
    var raw = showCode() ? el('details', {class:'advanced'}, el('summary', {text: t('settings.resolved_json')}),
      el('pre', {class:'code', dir:'ltr', text: JSON.stringify(resolved, null, 2)})) : null;
    body.appendChild(el('div', {class:'card stack'}, general, behaviour, budget, approvals, advanced, raw,
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

// Who is signed in, signing in another device with a one-time code, and (admin) handing a new account over.
function accountCard(){
  var me = STATE.me || {};
  var card = el('div', {class:'card stack', id:'account-card'});
  var codeBox = el('div', {class:'stack', role:'status'});
  function showLoginCode(c, forEmail){
    clear(codeBox);
    codeBox.appendChild(el('p', {class:'muted', text: forEmail ? t('auth.code_for', {email: forEmail}) : t('auth.code_own')}));
    codeBox.appendChild(el('code', {class:'login-code', dir:'ltr', text: c.code}));
    codeBox.appendChild(el('p', {class:'muted', text: t('auth.code_expires', {time: fmtTime(c.expires_at)})}));
  }
  var codeBtn = el('button', {class:'btn ghost sm', text: t('auth.new_device')});
  codeBtn.addEventListener('click', function(){
    api('/auth/login-codes', {method:'POST', json:{}}).then(function(c){ showLoginCode(c); })
      .catch(function(e){ clear(codeBox); codeBox.appendChild(el('p', {class:'err muted', text: e.message})); });
  });
  var outBtn = el('button', {class:'btn ghost sm', text: t('auth.sign_out')});
  outBtn.addEventListener('click', function(){
    api('/auth/logout', {method:'POST'}).then(function(){ STATE.userId = null; STATE.me = null; boot(); });
  });
  card.appendChild(el('h3', {text: t('auth.account')}));
  card.appendChild(el('p', {class:'muted'}, el('span', {dir:'ltr', text: me.email || ''}),
    me.is_admin ? el('span', {class:'chip', style:'margin-inline-start:8px', text: t('auth.admin')}) : null));
  card.appendChild(el('div', {class:'btn-row'}, codeBtn, outBtn));
  if (me.is_admin){
    var email = el('input', {type:'email', dir:'ltr', placeholder:'name@example.com'});
    var addStatus = el('p', {class:'err muted', role:'status'});
    var addBtn = el('button', {class:'btn sm', text: t('auth.add_account')});
    addBtn.addEventListener('click', function(){
      if (!email.value.trim()){ addStatus.textContent = t('auth.email_needed'); return; }
      addBtn.disabled = true; addStatus.textContent = '';
      api('/users', {method:'POST', json:{email: email.value.trim()}}).then(function(u){
        return api('/auth/login-codes', {method:'POST', json:{user_id: u.id}}).then(function(c){ showLoginCode(c, u.email); });
      }).then(function(){ email.value = ''; addBtn.disabled = false; })
        .catch(function(e){ addStatus.textContent = e.message; addBtn.disabled = false; });
    });
    card.appendChild(el('label', {class:'f'}, t('auth.add_account_label'), email));
    card.appendChild(el('div', {class:'btn-row'}, addBtn));
    card.appendChild(addStatus);
  }
  card.appendChild(codeBox);
  return card;
}

// The owner's own display preferences: saved at once, to the account; they never touch a setting or a permission.
function preferencesCard(){
  var card = el('div', {class:'card stack', id:'prefs-card'});
  var status = el('p', {class:'muted', role:'status'});
  function save(patch){
    status.className = 'muted'; status.textContent = '';
    return savePreferences(patch).then(function(){ draw(); status.textContent = t('common.saved'); })
      .catch(function(e){ status.className = 'err muted'; status.textContent = e.message; });
  }
  function draw(){
    clear(card);
    var p = prefs();
    var stored = STATE.prefs || {};
    var lang = el('select', {id:'pref-lang'});
    I18N.LOCALES.forEach(function(loc){ lang.appendChild(el('option', {value: loc, lang: loc}, I18N.NATIVE_NAMES[loc])); });
    lang.value = I18N.locale();
    lang.addEventListener('change', function(){ if (!changeLanguage(lang.value)) lang.value = I18N.locale(); });

    var level = el('select', {id:'pref-level'});
    AUDIENCE_LEVELS.forEach(function(v){ level.appendChild(el('option', {value: v}, lbl('level', v))); });
    level.value = p.audience_level;
    level.addEventListener('change', function(){ save({audience_level: level.value}); });

    var code = el('select', {id:'pref-code'});
    var followLabel = t('prefs.code_follow', {value: lbl('code', {ordinary:'hidden', professional:'hidden', developer:'visible'}[p.audience_level])});
    code.appendChild(el('option', {value: ''}, followLabel));
    ['hidden', 'visible'].forEach(function(v){ code.appendChild(el('option', {value: v}, lbl('code', v))); });
    code.value = stored.code_visibility || '';
    code.addEventListener('change', function(){ save({code_visibility: code.value || null}); });

    card.appendChild(el('h3', {text: t('prefs.title')}));
    card.appendChild(el('p', {class:'muted', text: t('prefs.sub')}));
    var theme = el('select', {id:'pref-theme'});
    ['light', 'dark'].forEach(function(v){ theme.appendChild(el('option', {value: v}, lbl('theme', v))); });
    theme.value = I18N.theme();
    theme.addEventListener('change', function(){ I18N.setTheme(theme.value); });
    card.appendChild(el('div', {class:'grid grid-3'},
      el('label', {class:'f'}, t('prefs.theme'), theme),
      el('label', {class:'f'}, t('prefs.language'), lang),
      el('label', {class:'f'}, t('prefs.level'), level),
      el('label', {class:'f'}, t('prefs.code'), code)));
    card.appendChild(el('p', {class:'muted', text: t('level_help.' + p.audience_level)}));
    if (STATE.prefs && !STATE.prefs.audience_level){
      // A proposal is not the owner's answer until they confirm it.
      card.appendChild(el('div', {class:'row'},
        el('span', {class:'muted', text: t('prefs.level_proposed', {level: lbl('level', p.audience_level)})}),
        el('button', {class:'btn ghost sm', onclick: function(){ save({audience_level: p.audience_level}); }}, t('prefs.confirm_level'))));
    }
    card.appendChild(status);
  }
  draw();
  return card;
}

/* ============================== observability ============================== */

function renderObservability(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:t('nav.observability')})));
  var picker = el('div', {class:'card'});
  var body = el('div', {class:'stack'});
  root.appendChild(picker);
  root.appendChild(body);
  picker.appendChild(loadingBox());

  fetchProjects().then(function(ps){
    clear(picker);
    if (!ps.length){ picker.appendChild(el('p', {class:'muted', text:t('common.no_projects')})); return; }
    var sel = el('select', {}); ps.forEach(function(p){ sel.appendChild(el('option', {value:p.id}, p.title)); });
    picker.appendChild(el('label', {class:'f'}, t('common.project'), sel));
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
    var evCard = el('div', {class:'card stack'}, el('h3', {text:t('common.events')}));
    if (!events.length) evCard.appendChild(el('p', {class:'muted', text:t('common.no_events')}));
    else events.slice().reverse().forEach(function(e){ evCard.appendChild(eventRow(e)); });
    body.appendChild(evCard);

    var callCard = el('div', {class:'card stack'}, el('h3', {text:t('obs.model_calls')}));
    if (!calls.length){ callCard.appendChild(el('p', {class:'muted', text:t('obs.no_calls')})); body.appendChild(callCard); return; }
    var wrap = el('div', {class:'table-wrap'});
    var table = el('table', {},
      el('thead', {}, el('tr', {}, [t('obs.role'),t('common.provider'),t('common.model'),t('obs.input'),t('obs.output'),t('obs.web_search'),t('obs.cost'),t('obs.status'),t('obs.time')].map(function(h){ return el('th', {text:h}); }))),
      el('tbody', {}, calls.map(function(c){
        return el('tr', {},
          el('td', {text: c.role}), el('td', {text: c.provider}), el('td', {text: c.model}),
          el('td', {text: fmtNum(c.input_tokens)}), el('td', {text: fmtNum(c.output_tokens)}),
          el('td', {text: fmtNum(c.web_searches)}),
          el('td', {text: fmtMoney(c.cost_usd)}),
          el('td', {}, c.ok ? el('span', {class:'chip', text:t('obs.ok')}) : el('span', {class:'chip risk-high', text:t('obs.failed')})),
          el('td', {text: fmtTime(c.created_at)}));
      })));
    wrap.appendChild(table);
    callCard.appendChild(wrap);
    body.appendChild(callCard);
  }
}

/* ============================== benchmark ============================== */

var BENCH_EFFORTS = ['', 'low', 'medium', 'high'];

function renderBenchmark(root){
  root.appendChild(el('div', {class:'page-head'}, el('h2', {text:t('nav.benchmark')})));
  var formCard = el('div', {class:'card stack'});
  var rows = el('div', {class:'stack', style:'gap:10px'});
  var status = el('p', {class:'err muted'});
  var addBtn = el('button', {class:'btn ghost sm', type:'button', text:t('bench.add_route')});
  var runBtn = el('button', {class:'btn', text:t('bench.run')});
  var body = el('div', {class:'stack'});

  function routeRow(defaults){
    defaults = defaults || {};
    var provider = el('select', {});
    PROVIDERS.forEach(function(p){ provider.appendChild(el('option', {value:p, text:p})); });
    provider.value = defaults.provider || 'anthropic';
    var model = el('input', {type:'text', placeholder:'claude-opus-5', value: defaults.model || ''});
    var effort = el('select', {});
    BENCH_EFFORTS.forEach(function(e){ effort.appendChild(el('option', {value:e, text:lbl('effort', e)})); });
    effort.value = defaults.effort || '';
    var removeBtn = el('button', {class:'btn ghost sm', type:'button', text:t('common.remove')});
    var row = el('div', {class:'grid grid-3', style:'align-items:end;gap:8px'},
      el('label', {class:'f'}, t('common.provider'), provider),
      el('label', {class:'f'}, t('common.model'), model),
      el('div', {class:'row', style:'gap:8px;align-items:end'},
        el('label', {class:'f', style:'flex:1'}, t('bench.effort'), effort), removeBtn));
    row._route = function(){ return {provider: provider.value, model: model.value.trim(), effort: effort.value || null}; };
    removeBtn.addEventListener('click', function(){ if (rows.children.length > 1) rows.removeChild(row); });
    return row;
  }

  rows.appendChild(routeRow({provider:'anthropic', model:'claude-opus-5', effort:'low'}));
  rows.appendChild(routeRow({provider:'anthropic', model:'claude-sonnet-5', effort:'low'}));
  addBtn.addEventListener('click', function(){ if (rows.children.length < 5) rows.appendChild(routeRow({})); });

  runBtn.addEventListener('click', function(){
    var routes = Array.prototype.map.call(rows.children, function(r){ return r._route(); });
    if (routes.some(function(r){ return !r.model; })){ status.textContent = t('bench.model_required'); return; }
    runBtn.disabled = true; status.textContent = '';
    api('/benchmarks/run', {method:'POST', json:{routes: routes}})
      .then(function(){ runBtn.disabled = false; load(); })
      .catch(function(e){ status.textContent = e.message; runBtn.disabled = false; });
  });

  formCard.appendChild(el('p', {class:'muted',
    text:t('bench.explain')}));
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
      el('td', {text:r.provider}), el('td', {text:r.model}), el('td', {text: lbl('effort', r.effort)}),
      el('td', {text: fmtNum(r.input_tokens)}), el('td', {text: fmtNum(r.output_tokens)}),
      el('td', {text: fmtMoney(r.cost_usd)}), el('td', {text: fmtNum(r.duration_ms) + ' ms'}),
      el('td', {}, r.schema_valid
        ? el('span', {class:'chip status-COMPLETED', text:t('bench.valid')})
        : el('span', {class:'chip status-FAILED', title: r.error || '', text:t('bench.invalid')})),
      el('td', {}, ratingCell(r)));
  }

  function load(){
    clear(body); body.appendChild(loadingBox());
    api('/benchmarks').then(function(runs){
      clear(body);
      if (!runs.length){
        body.appendChild(emptyBox('📈', t('bench.empty_title'), t('bench.empty_sub')));
        return;
      }
      var wrap = el('div', {class:'table-wrap'});
      wrap.appendChild(el('table', {},
        el('thead', {}, el('tr', {}, [t('common.provider'),t('common.model'),t('bench.effort'),t('obs.input'),t('obs.output'),t('obs.cost'),t('obs.time'),'schema',t('bench.rating')]
          .map(function(h){ return el('th', {text:h}); }))),
        el('tbody', {}, runs.map(benchmarkRow))));
      body.appendChild(wrap);
    }).catch(function(e){ clear(body); body.appendChild(errorBox(e.message, load)); });
  }
  load();
}

/* ============================== boot ============================== */

var booted = false;

function boot(){
  root = document.getElementById('page-root');
  if (!booted){
    booted = true;
    document.querySelectorAll('.nav-item').forEach(function(b){
      b.addEventListener('click', function(){ go(b.dataset.page); });
    });
    window.addEventListener('hashchange', route);
    setupLanguagePicker();
    root.addEventListener('focusin', rememberOriginal);
  }
  I18N.apply();
  ensureUser().then(function(me){
    if (!me){
      // Not signed in: the language first if this browser never chose one (kept here only), then the sign-in.
      if (!I18N.storedLocale()){ clear(root); renderLanguageChooser(root, function(){ renderSignIn(root); }, false); }
      else renderSignIn(root);
      return;
    }
    // Without the account's preferences the UI still works, in this browser's language.
    return loadPreferences().catch(function(){ return null; }).then(function(p){
      syncLanguagePicker();
      if (p && !p.ui_locale && !I18N.storedLocale()){
        clear(root);
        renderLanguageChooser(root, function(){ route(); }, true);
        return;
      }
      route();
    });
  }).catch(function(e){
    clear(root);
    root.appendChild(errorBox(t('boot.server_unreachable', {message: e.message}), boot));
  });
}

document.addEventListener('DOMContentLoaded', boot);
})();
