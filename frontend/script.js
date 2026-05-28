const API_BASE = window.QUALIA_API_BASE || window.location.origin;
const TOKEN_KEY = "qualia_token";
const AUTH_KEY = "qualia_auth";

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  auth: JSON.parse(localStorage.getItem(AUTH_KEY) || "null"),
  users: [],
  appointments: [],
  teachers: [],
  disciplines: [],
  rosterStudents: [],
  currentDisciplineId: null,
  adminTab: "main",
  academicYearFilter: "all",
  calendarMonth: "",
  config: null,
  currentDashboardUserId: null,
  currentDashboardData: null,
  carouselIndex: 0,
  carouselTimer: null,
  resetToken: new URLSearchParams(window.location.search).get("reset_token") || "",
};

const resources = [
  {
    title: "Ministério da Saúde",
    description: "Conteúdos públicos sobre prevenção, atividade física, alimentação e promoção da saúde.",
    url: "https://www.gov.br/saude/pt-br",
  },
  {
    title: "OPAS Brasil",
    description: "Materiais e campanhas sobre qualidade de vida, bem-estar e saúde pública.",
    url: "https://www.paho.org/pt/brasil",
  },
  {
    title: "OMS",
    description: "Recomendações globais sobre atividade física, estilo de vida e saúde.",
    url: "https://www.who.int",
  },
  {
    title: "SBME",
    description: "Referências úteis sobre medicina do esporte e exercício.",
    url: "https://www.medicinadoesporte.org.br",
  },
];

const appointmentSlots = {
  0: ["13:50", "14:20", "14:50", "15:20", "15:50", "16:20", "16:50", "17:20"],
  1: ["08:20", "08:50", "09:20", "09:50", "10:20", "10:50", "11:20"],
  2: ["10:20", "10:50", "11:20"],
};

function $(id) {
  return document.getElementById(id);
}

function showToast(message, isError = false) {
  const toast = $("toast");
  toast.textContent = normalizeMessage(message);
  toast.classList.remove("hidden");
  toast.style.borderColor = isError ? "rgba(187, 102, 102, 0.22)" : "rgba(79, 166, 111, 0.24)";
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add("hidden"), 3400);
}

function normalizeMessage(message) {
  if (typeof message === "string") return message;
  if (Array.isArray(message)) {
    return message.map((item) => normalizeMessage(item)).filter(Boolean).join(" | ");
  }
  if (message && typeof message === "object") {
    if (Array.isArray(message.detail)) return normalizeMessage(message.detail);
    if (typeof message.detail === "string") return message.detail;
    if (typeof message.message === "string") return message.message;
    if (typeof message.msg === "string") return message.msg;
    if (message.loc && message.msg) return `${message.loc.join(" > ")}: ${message.msg}`;
    try {
      return JSON.stringify(message);
    } catch (error) {
      return "Erro ao processar a mensagem.";
    }
  }
  return String(message ?? "");
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!response.ok) {
    let detail = "Erro ao processar a requisição.";
    try {
      const body = await response.json();
      detail = normalizeMessage(body.detail || body) || detail;
    } catch (error) {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function setAuth(auth) {
  state.auth = auth;
  state.token = auth?.token || "";
  if (state.token) {
    localStorage.setItem(TOKEN_KEY, state.token);
    localStorage.setItem(AUTH_KEY, JSON.stringify(auth));
  } else {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(AUTH_KEY);
  }
}

function showView(viewId) {
  ["public-view", "login-view", "admin-view", "user-view"].forEach((id) => $(id).classList.add("hidden"));
  $(viewId).classList.remove("hidden");
}

function setAdminTab(tab) {
  state.adminTab = tab;
  document.querySelectorAll(".admin-main-section").forEach((el) => el.classList.toggle("hidden", tab !== "main"));
  document.querySelectorAll(".admin-academic-section").forEach((el) => el.classList.toggle("hidden", tab !== "academic"));
  $("admin-main-tab-btn").classList.toggle("active", tab === "main");
  $("admin-academic-tab-btn").classList.toggle("active", tab === "academic");
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("pt-BR");
}

function parseNumber(value) {
  if (value === "" || value == null) return null;
  const normalized = String(value).replace(",", ".").trim();
  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

function createListItems(containerId, items) {
  const container = $(containerId);
  container.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    container.appendChild(li);
  });
}

function setFormFeedback(id, message, isError = false) {
  const el = $(id);
  if (!el) return;
  el.textContent = normalizeMessage(message);
  el.classList.remove("hidden", "error", "success");
  el.classList.add(isError ? "error" : "success");
}

function clearFormFeedback(id) {
  const el = $(id);
  if (!el) return;
  el.textContent = "";
  el.classList.add("hidden");
  el.classList.remove("error", "success");
}

function readStoredValue(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (error) {
    return fallback;
  }
}

function writeStoredValue(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function getWeekAnchor(date = new Date()) {
  const current = new Date(date);
  current.setHours(0, 0, 0, 0);
  const weekday = (current.getDay() + 6) % 7;
  current.setDate(current.getDate() - weekday);
  return current;
}

function getWeekTrackerKey(userId) {
  return `qualia_weekly_tracker_${userId}_${toLocalIsoDate(getWeekAnchor())}`;
}

function getWaterTrackerKey(userId) {
  return `qualia_water_tracker_${userId}_${toLocalIsoDate(new Date())}`;
}

function getWeeklyTracker(userId, plan) {
  const stored = readStoredValue(getWeekTrackerKey(userId), {});
  return plan.map((_, index) => Boolean(stored[index]));
}

function saveWeeklyTracker(userId, checks) {
  const payload = {};
  checks.forEach((checked, index) => {
    payload[index] = checked;
  });
  writeStoredValue(getWeekTrackerKey(userId), payload);
}

function getWaterTracker(userId) {
  const stored = readStoredValue(getWaterTrackerKey(userId), { total: 0 });
  return { total: Number(stored.total || 0) };
}

function saveWaterTracker(userId, total) {
  writeStoredValue(getWaterTrackerKey(userId), { total });
}

function getWeeklyEncouragement(rate) {
  if (rate >= 1) return "Parabéns! Você cumpriu todas as metas da semana e está construindo constância.";
  if (rate >= 0.6) return "Muito bem. Você já está mantendo uma rotina boa, continue firme nos próximos dias.";
  if (rate > 0) return "Bom começo. Cada atividade marcada fortalece o hábito e aproxima do próximo resultado melhor.";
  return "Comece com uma meta simples hoje. O mais importante agora é ganhar ritmo e confiança.";
}

function setMetricBar(fillId, labelId, value, maxValue, suffix = "") {
  const safeValue = value == null || Number.isNaN(value) ? 0 : Number(value);
  const width = Math.max(0, Math.min(100, (safeValue / maxValue) * 100));
  $(fillId).style.width = `${width}%`;
  $(labelId).textContent = value == null || Number.isNaN(value) ? "--" : `${safeValue.toFixed(1)}${suffix}`;
}

function formatMetricValue(value, suffix = "", digits = 1) {
  if (value == null || Number.isNaN(Number(value))) return "--";
  return `${Number(value).toFixed(digits)}${suffix}`;
}

function renderWeeklyPlan(plan, userId) {
  const container = $("weekly-plan-list");
  if (!plan.length || !userId) {
    container.innerHTML = "<p class='muted'>As atividades aparecerão aqui quando houver avaliação salva.</p>";
    $("weekly-progress-text").textContent = "Sem plano semanal ainda.";
    $("weekly-encouragement").textContent = "";
    $("weekly-completion-rate").textContent = "0%";
    return;
  }

  const checks = getWeeklyTracker(userId, plan);
  const completed = checks.filter(Boolean).length;
  const rate = completed / plan.length;

  container.innerHTML = plan.map((item, index) => `
    <button class="habit-item ${checks[index] ? "done" : ""}" type="button" data-weekly-toggle="${index}">
      <span class="habit-check">${checks[index] ? "✓" : ""}</span>
      <span>${item}</span>
    </button>
  `).join("");

  $("weekly-progress-text").textContent = `${completed} de ${plan.length} metas concluídas nesta semana.`;
  $("weekly-encouragement").textContent = getWeeklyEncouragement(rate);
  $("weekly-completion-rate").textContent = `${Math.round(rate * 100)}%`;
}

function renderWaterTracker(targetLiters, userId) {
  const tracker = userId ? getWaterTracker(userId) : { total: 0 };
  const total = tracker.total;
  const target = Number(targetLiters || 0);
  const progress = target > 0 ? Math.min(100, (total / target) * 100) : 0;

  $("water-target").textContent = target > 0
    ? `Sua meta diária estimada é ${target.toFixed(1)} L de água, calculada pelo peso corporal.`
    : "Sem meta calculada.";
  $("water-progress-fill").style.width = `${progress}%`;
  $("water-progress-label").textContent = `${Math.round(progress)}%`;
  $("water-total-today").textContent = `${total.toFixed(1)} L`;

  if (target <= 0) {
    $("water-feedback").textContent = "A meta de hidratação aparecerá quando houver uma avaliação com peso salvo.";
    return;
  }

  if (total >= target) {
    $("water-feedback").textContent = "Parabéns! Você bateu a meta de água de hoje.";
  } else {
    const missing = Math.max(target - total, 0);
    $("water-feedback").textContent = `Faltam ${missing.toFixed(1)} L para atingir sua meta de hoje.`;
  }
}

function renderResources() {
  $("resources-list").innerHTML = resources.map((item) => `
    <article class="resource-card">
      <strong>${item.title}</strong>
      <p>${item.description}</p>
      <a href="${item.url}" target="_blank" rel="noopener noreferrer">Acessar site</a>
    </article>
  `).join("");
}

async function loadConfig() {
  state.config = await api("/config");
  $("google-hint").textContent = "Use o e-mail e a senha cadastrados na plataforma.";
}

async function loadHealth() {
  try {
    await api("/health");
    $("hero-health").textContent = "API online";
    $("hero-status-title").textContent = "API online";
  } catch (error) {
    $("hero-health").textContent = "API offline";
    $("hero-status-title").textContent = "API offline";
  }
}

function setCarousel(index) {
  const slides = [...document.querySelectorAll(".carousel-slide")];
  const dots = [...document.querySelectorAll(".carousel-dot")];
  slides.forEach((slide, current) => slide.classList.toggle("active", current === index));
  dots.forEach((dot, current) => dot.classList.toggle("active", current === index));
  state.carouselIndex = index;
}

function startCarousel() {
  const slides = [...document.querySelectorAll(".carousel-slide")];
  if (!slides.length) return;
  clearInterval(state.carouselTimer);
  state.carouselTimer = setInterval(() => {
    const next = (state.carouselIndex + 1) % slides.length;
    setCarousel(next);
  }, 4200);
}

function formatDateLabel(date) {
  return date.toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "2-digit" });
}

function toLocalIsoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function populateAppointmentDates() {
  const select = $("appt-date");
  if (!select) return;
  const options = [];
  const today = new Date();
  for (let add = 0; add < 35; add += 1) {
    const date = new Date(today);
    date.setDate(today.getDate() + add);
    const weekday = date.getDay();
    const mappedWeekday = weekday === 0 ? 7 : weekday - 1;
    if (!(mappedWeekday in appointmentSlots)) continue;
    const isoDate = toLocalIsoDate(date);
    options.push(`<option value="${isoDate}">${formatDateLabel(date)}</option>`);
  }
  select.innerHTML = options.join("");
  populateAppointmentTimes();
}

function populateAppointmentTimes() {
  const dateValue = $("appt-date").value;
  const timeSelect = $("appt-time");
  if (!dateValue || !timeSelect) return;
  const weekday = new Date(`${dateValue}T12:00:00`).getDay();
  const mappedWeekday = weekday === 0 ? 7 : weekday - 1;
  const times = appointmentSlots[mappedWeekday] || [];
  timeSelect.innerHTML = times.map((time) => `<option value="${time}">${time}</option>`).join("");
}

async function handleLogin(event) {
  event.preventDefault();
  try {
    const auth = await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({
        email: $("login-email").value.trim(),
        password: $("login-password").value.trim(),
      }),
    });
    setAuth(auth);
    showToast("Login realizado com sucesso.");
    await bootstrapAuthenticated();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function handleForgotPassword(event) {
  event.preventDefault();
  clearFormFeedback("password-recovery-feedback");
  try {
    await api("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email: $("forgot-email").value.trim() }),
    });
    const message = "Se o e-mail estiver cadastrado, enviaremos um link de recuperação.";
    setFormFeedback("password-recovery-feedback", message);
    showToast("Verifique seu e-mail para redefinir a senha.");
  } catch (error) {
    setFormFeedback("password-recovery-feedback", error.message, true);
    showToast(error.message, true);
  }
}

async function handleResetPassword(event) {
  event.preventDefault();
  clearFormFeedback("password-recovery-feedback");
  const password = $("reset-password").value.trim();
  const confirm = $("reset-password-confirm").value.trim();
  if (password !== confirm) {
    setFormFeedback("password-recovery-feedback", "As senhas não conferem.", true);
    return;
  }
  try {
    const response = await api("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token: state.resetToken, password }),
    });
    setFormFeedback("password-recovery-feedback", response.message);
    showToast(response.message);
    state.resetToken = "";
    window.history.replaceState({}, document.title, window.location.pathname);
    $("reset-password-form").classList.add("hidden");
    $("login-form").classList.remove("hidden");
    $("forgot-password-btn").classList.remove("hidden");
  } catch (error) {
    setFormFeedback("password-recovery-feedback", error.message, true);
    showToast(error.message, true);
  }
}

async function handleLogout() {
  try {
    if (state.token) await api("/auth/logout", { method: "POST" });
  } catch (error) {
    console.warn(error);
  }
  setAuth(null);
  showView("public-view");
}

function userCard(user) {
  const score = user.ultimo_score != null ? Number(user.ultimo_score).toFixed(1) : "--";
  return `
    <div class="user-row">
      <div>
        <strong>${user.nome}</strong>
        <div>${user.email}</div>
        <div class="muted">Idade ${user.idade ?? "-"} | Sexo ${user.sexo ?? "-"} | Altura ${user.altura_cm ?? "-"} cm</div>
      </div>
      <div>
        <div class="news-meta">Último score: ${score}</div>
        <div class="muted">Última avaliação: ${formatDate(user.ultima_avaliacao_em)}</div>
      </div>
      <div class="row-actions">
        <button class="btn ghost" type="button" data-view-user="${user.id}">Ver painel</button>
        ${user.role === "admin" ? "" : `<button class="btn danger" type="button" data-delete-user="${user.id}">Excluir</button>`}
      </div>
    </div>
  `;
}

function appointmentBadge(status) {
  if (status === "enviado") return "<span class='appointment-badge'>E-mail enviado</span>";
  if (status === "processando_envio") return "<span class='appointment-badge warn'>Enviando confirmação</span>";
  if (status === "pendente_configuração") return "<span class='appointment-badge warn'>SMTP pendente</span>";
  return "<span class='appointment-badge error'>Falha no envio</span>";
}

function appointmentCard(item) {
  return `
    <div class="appointment-row">
      <div>
        <strong>${item.nome}</strong>
        <div>${item.email}</div>
        <div class="muted">${item.telefone}</div>
      </div>
      <div>
        <strong>${item.data_agendada}</strong>
        <div class="muted">${item.horario_agendado}</div>
      </div>
      <div>
        <strong>${item.local}</strong>
        <div class="muted">Criado em ${formatDate(item.created_at)}</div>
      </div>
      <div>
        ${appointmentBadge(item.email_status)}
        <div class="row-actions compact-actions">
          <button class="btn danger" type="button" data-cancel-appointment="${item.id}">Desmarcar</button>
        </div>
      </div>
    </div>
  `;
}

function renderAppointmentCalendar(appointments) {
  const container = $("admin-calendar");
  const today = new Date();
  const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
  const firstDay = (monthStart.getDay() + 6) % 7;
  const calendarStart = new Date(monthStart);
  calendarStart.setDate(monthStart.getDate() - firstDay);
  const weekdays = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

  const cells = [];
  for (let i = 0; i < 42; i += 1) {
    const current = new Date(calendarStart);
    current.setDate(calendarStart.getDate() + i);
    const iso = toLocalIsoDate(current);
    const items = appointments.filter((item) => item.data_agendada === iso);
    const isToday = iso === toLocalIsoDate(today);
    cells.push(`
      <td class="calendar-day ${current.getMonth() === today.getMonth() ? "" : "muted-day"} ${isToday ? "today" : ""}">
        <div class="calendar-day-top">
          <strong>${current.getDate()}</strong>
          <span>${items.length ? `${items.length} ag.` : ""}</span>
        </div>
        <div class="calendar-events">
          ${items.slice(0, 3).map((item) => `<div class="calendar-event">${item.horario_agendado}<span>${item.nome}</span></div>`).join("") || "<div class='muted calendar-empty'>Sem agendamento</div>"}
        </div>
      </td>
    `);
  }
  const rows = [];
  for (let i = 0; i < cells.length; i += 7) {
    rows.push(`<tr>${cells.slice(i, i + 7).join("")}</tr>`);
  }
  container.innerHTML = `
    <div class="calendar-table-wrap">
      <table class="calendar-table">
        <thead>
          <tr>${weekdays.map((day) => `<th class="calendar-weekday">${day}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows.join("")}
        </tbody>
      </table>
    </div>
  `;
}

function populateTeacherOptions() {
  $("acad-professor").innerHTML = state.teachers
    .map((teacher) => `<option value="${teacher.nome}">${teacher.nome}</option>`)
    .join("");
}

function populateAcademicYearFilter() {
  const select = $("acad-year-filter");
  const years = [...new Set(state.disciplines.map((item) => String(item.ano)))].sort((a, b) => Number(b) - Number(a));
  select.innerHTML = `<option value="all">Todos</option>${years.map((year) => `<option value="${year}">${year}</option>`).join("")}`;
  select.value = years.includes(String(state.academicYearFilter)) ? String(state.academicYearFilter) : "all";
}

function resetDisciplineForm() {
  $("admin-discipline-form").reset();
  delete $("admin-discipline-form").dataset.editingId;
  $("acad-ano").value = new Date().getFullYear();
  $("acad-cancel-edit").classList.add("hidden");
}

function renderDisciplineCards() {
  const filtered = state.academicYearFilter === "all"
    ? state.disciplines
    : state.disciplines.filter((item) => String(item.ano) === String(state.academicYearFilter));

  if (!filtered.length) {
    $("disciplines-list").innerHTML = "<p class='muted'>Nenhuma disciplina cadastrada.</p>";
    return;
  }
  const byYear = filtered.reduce((acc, item) => {
    acc[item.ano] = acc[item.ano] || [];
    acc[item.ano].push(item);
    return acc;
  }, {});
  const years = Object.keys(byYear).sort((a, b) => Number(b) - Number(a));
  $("disciplines-list").innerHTML = years.map((year) => `
    <section class="discipline-year-group">
      <div class="discipline-year-header">
        <h4>${year}</h4>
        <span class="discipline-year-count">${byYear[year].length} turma(s)</span>
      </div>
      ${Object.entries(byYear[year].reduce((acc, item) => {
        acc[item.professor_nome] = acc[item.professor_nome] || [];
        acc[item.professor_nome].push(item);
        return acc;
      }, {})).map(([professor, items]) => `
        <article class="discipline-professor-group">
          <header>
            <strong>${professor}</strong>
            <span>${items.length} turma(s)</span>
          </header>
          <div class="discipline-year-list">
            ${items.map((item) => `
              <div class="discipline-card ${state.currentDisciplineId === item.id ? "selected" : ""}">
                <button class="discipline-card-main" type="button" data-select-discipline="${item.id}">
                  <span>${item.codigo}</span>
                  <strong>${item.nome}</strong>
                  <div>${item.turma_nome}</div>
                  <div class="muted">${item.horario}</div>
                  <div class="muted">${item.total_alunos} alunos ${item.arquivo_referencia ? `· ${item.arquivo_referencia}` : ""}</div>
                </button>
                <div class="row-actions">
                  <button class="btn ghost" type="button" data-edit-discipline="${item.id}">Editar</button>
                  <button class="btn danger" type="button" data-delete-discipline="${item.id}">Excluir</button>
                </div>
              </div>
            `).join("")}
          </div>
        </article>
      `).join("")}
    </section>
  `).join("");
}

function populateDisciplineSelect() {
  const select = $("eval-discipline");
  select.innerHTML = `<option value="">Selecionar turma</option>${state.disciplines
    .map((item) => `<option value="${item.id}">${item.ano} · ${item.professor_nome} · ${item.turma_nome}</option>`)
    .join("")}`;
}

function renderRosterStudents() {
  $("roster-list").innerHTML = state.rosterStudents.map((student) => `
    <div class="roster-row">
      <div>
        <strong>${student.nome}</strong>
        <div class="muted">${student.matricula || "Sem matrícula"} ${student.linked_user_email ? `· ${student.linked_user_email}` : "· perfil ainda não vinculado"}</div>
      </div>
      <div class="muted">${student.total_testes} teste(s)</div>
      <div class="row-actions">
        <button class="btn ghost" type="button" data-use-roster="${student.id}">Usar no teste</button>
        ${student.linked_user_id ? `<button class="btn ghost" type="button" data-view-user="${student.linked_user_id}">Ver painel</button>` : ""}
      </div>
    </div>
  `).join("") || "<p class='muted'>Nenhum aluno importado nesta turma.</p>";
}

function applyRosterImportResult(result, fallbackMessage) {
  state.rosterStudents = result.alunos || [];
  renderRosterStudents();
  populateRosterSelect();
  const message = result.mensagem || fallbackMessage;
  setFormFeedback("roster-import-feedback", message);
  showToast(message);
}

function populateRosterSelect() {
  $("eval-roster").innerHTML = `<option value="">Selecionar aluno</option>${state.rosterStudents
    .map((student) => `<option value="${student.id}">${student.nome}</option>`)
    .join("")}`;
}

function fillEvaluationFromRoster(studentId) {
  const student = state.rosterStudents.find((item) => String(item.id) === String(studentId));
  if (!student) return;
  $("eval-roster").value = String(student.id);
  $("eval-nome").value = student.nome || "";
  $("eval-email").value = student.linked_user_email || "";
  $("eval-discipline").value = state.currentDisciplineId ? String(state.currentDisciplineId) : "";
  showToast(`Aluno ${student.nome} carregado no formulário.`);
}

async function selectDiscipline(disciplineId) {
  state.currentDisciplineId = Number(disciplineId);
  $("roster-discipline-id").value = String(disciplineId);
  const discipline = state.disciplines.find((item) => item.id === Number(disciplineId));
  $("selected-discipline-title").textContent = discipline
    ? `${discipline.professor_nome} · ${discipline.nome} · ${discipline.turma_nome}`
    : "Escolha uma disciplina para importar e usar os alunos";
  state.rosterStudents = await api(`/admin/disciplines/${disciplineId}/students`);
  renderRosterStudents();
  populateRosterSelect();
  $("eval-discipline").value = String(disciplineId);
}

async function runEvaluationSearch() {
  const query = $("eval-search-input").value.trim();
  if (query.length < 2) {
    $("eval-search-results").innerHTML = "<p class='muted'>Digite pelo menos 2 letras para buscar.</p>";
    return;
  }
  const results = await api(`/admin/evaluations/search?q=${encodeURIComponent(query)}`);
  $("eval-search-results").innerHTML = results.map((item) => `
    <div class="search-row">
      <div>
        <strong>${item.nome}</strong>
        <div class="muted">${item.email}</div>
      </div>
      <div class="muted">${item.tipo_avaliacao} · ${formatDate(item.created_at)}</div>
      <div class="row-actions">
        <span class="news-meta">Score ${item.score_ia != null ? Number(item.score_ia).toFixed(1) : "--"}</span>
        <button class="btn ghost" type="button" data-view-user="${item.user_id}">Ver painel</button>
      </div>
    </div>
  `).join("") || "<p class='muted'>Nenhum teste encontrado para essa busca.</p>";
}

function updateUserEmailDatalist() {
  const list = $("users-email-list");
  if (!list) return;
  list.innerHTML = state.users
    .filter((user) => user.role !== "admin")
    .map((user) => `<option value="${user.email}">${user.nome}</option>`)
    .join("");
}

function updateEvaluationEntryMode() {
  const mode = $("eval-entry-mode").value;
  document.querySelectorAll(".eval-entry-group").forEach((group) => {
    group.classList.toggle("hidden", group.dataset.entryMode !== mode);
  });
  const turmaMode = mode === "turma";
  $("eval-discipline").disabled = !turmaMode;
  $("eval-roster").disabled = !turmaMode;
  $("eval-email").disabled = turmaMode;
  $("eval-nome").disabled = turmaMode;
}

function formatMonthOption(value) {
  const [year, month] = value.split("-");
  const date = new Date(Number(year), Number(month) - 1, 1);
  return date.toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
}

function populateCalendarMonthFilter(appointments) {
  const select = $("calendar-month-filter");
  const currentMonth = toLocalIsoDate(new Date()).slice(0, 7);
  const months = [...new Set([currentMonth, ...appointments.map((item) => String(item.data_agendada).slice(0, 7))])]
    .filter(Boolean)
    .sort()
    .reverse();
  if (!state.calendarMonth || !months.includes(state.calendarMonth)) {
    state.calendarMonth = currentMonth;
  }
  select.innerHTML = months.map((month) => `<option value="${month}">${formatMonthOption(month)}</option>`).join("");
  select.value = state.calendarMonth;
}

function renderAppointmentCalendar(appointments) {
  const container = $("admin-calendar");
  const monthValue = state.calendarMonth || toLocalIsoDate(new Date()).slice(0, 7);
  const [year, month] = monthValue.split("-").map(Number);
  const today = new Date();
  const monthStart = new Date(year, month - 1, 1);
  const firstDay = (monthStart.getDay() + 6) % 7;
  const calendarStart = new Date(monthStart);
  calendarStart.setDate(monthStart.getDate() - firstDay);
  const weekdays = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

  const cells = [];
  for (let i = 0; i < 42; i += 1) {
    const current = new Date(calendarStart);
    current.setDate(calendarStart.getDate() + i);
    const iso = toLocalIsoDate(current);
    const items = appointments.filter((item) => item.data_agendada === iso);
    const isToday = iso === toLocalIsoDate(today);
    const visibleItems = items.slice(0, 2);
    cells.push(`
      <td class="calendar-day ${current.getMonth() === (month - 1) ? "" : "muted-day"} ${isToday ? "today" : ""}">
        <div class="calendar-day-top">
          <strong>${current.getDate()}</strong>
          <span>${items.length ? `${items.length} ag.` : ""}</span>
        </div>
        <div class="calendar-events">
          ${visibleItems.map((item) => `<div class="calendar-event">${item.horario_agendado}<span>${item.nome}</span></div>`).join("") || "<div class='muted calendar-empty'>Sem agendamento</div>"}
          ${items.length > 2 ? `<div class="calendar-more">+ ${items.length - 2} agendamento(s)</div>` : ""}
        </div>
      </td>
    `);
  }
  const rows = [];
  for (let i = 0; i < cells.length; i += 7) {
    rows.push(`<tr>${cells.slice(i, i + 7).join("")}</tr>`);
  }
  container.innerHTML = `
    <div class="calendar-table-wrap">
      <table class="calendar-table">
        <thead>
          <tr>${weekdays.map((day) => `<th class="calendar-weekday">${day}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows.join("")}
        </tbody>
      </table>
    </div>
  `;
}

function updateEvaluationFormVisibility() {
  const selectedType = $("eval-tipo").value;
  document.querySelectorAll(".eval-group").forEach((group) => {
    const allowedTypes = (group.dataset.evalGroup || "").split(",");
    const shouldShow = allowedTypes.includes(selectedType);
    group.classList.toggle("hidden", !shouldShow);
  });
}

async function renderAdmin() {
  const [users, overview, appointments, teachers, disciplines] = await Promise.all([
    api("/admin/users"),
    api("/admin/overview"),
    api("/admin/appointments"),
    api("/admin/teachers"),
    api("/admin/disciplines"),
  ]);
  state.users = users;
  state.appointments = appointments;
  state.teachers = teachers;
  state.disciplines = disciplines;
  $("admin-users-count").textContent = String(overview.total_users);
  $("admin-evals-count").textContent = String(overview.total_evaluations);
  $("admin-users-list").innerHTML = users.map(userCard).join("") || "<p class='muted'>Nenhum usuário cadastrado.</p>";
  $("admin-appointments-list").innerHTML =
    appointments.map(appointmentCard).join("") || "<p class='muted'>Nenhum agendamento registrado.</p>";
  $("eval-search-results").innerHTML = "<p class='muted'>Busque por nome ou e-mail para encontrar testes rapidamente.</p>";
  setAdminTab(state.adminTab || "main");
  populateCalendarMonthFilter(appointments);
  renderAppointmentCalendar(appointments);
  populateTeacherOptions();
  populateAcademicYearFilter();
  renderDisciplineCards();
  populateDisciplineSelect();
  updateUserEmailDatalist();
  if (state.currentDisciplineId) {
    await selectDiscipline(state.currentDisciplineId);
  } else {
    $("selected-discipline-title").textContent = "Escolha uma disciplina para importar e usar os alunos";
    $("roster-list").innerHTML = "<p class='muted'>Selecione uma disciplina para ver a turma.</p>";
    $("eval-roster").innerHTML = "<option value=''>Selecionar aluno</option>";
  }
}

async function handleCreateUser(event) {
  event.preventDefault();
  try {
    await api("/admin/users", {
      method: "POST",
      body: JSON.stringify({
        nome: $("adm-nome").value.trim(),
        email: $("adm-email").value.trim(),
        cpf: $("adm-cpf").value.trim(),
        idade: Number($("adm-idade").value),
        sexo: $("adm-sexo").value,
        altura_cm: Number($("adm-altura").value),
        observacoes: $("adm-observacoes").value.trim(),
      }),
    });
    event.target.reset();
    $("adm-sexo").value = "M";
    showToast("Usuário cadastrado.");
    setFormFeedback("admin-user-feedback", "Usuário salvo com sucesso.");
    await renderAdmin();
  } catch (error) {
    showToast(error.message, true);
    setFormFeedback("admin-user-feedback", error.message, true);
  }
}

async function handleCreateDiscipline(event) {
  event.preventDefault();
  try {
    const editingId = $("admin-discipline-form").dataset.editingId || "";
    const discipline = await api(editingId ? `/admin/disciplines/${editingId}` : "/admin/disciplines", {
      method: editingId ? "PUT" : "POST",
      body: JSON.stringify({
        professor_nome: $("acad-professor").value,
        ano: Number($("acad-ano").value),
        nome: $("acad-nome").value.trim(),
        codigo: $("acad-codigo").value.trim(),
        horario: $("acad-horario").value.trim(),
        turma_nome: $("acad-turma").value.trim(),
        arquivo_referencia: $("acad-arquivo").value.trim(),
      }),
    });
    resetDisciplineForm();
    setFormFeedback("admin-discipline-feedback", editingId ? "Turma atualizada com sucesso." : "Disciplina salva com sucesso.");
    await renderAdmin();
    await selectDiscipline(discipline.id);
  } catch (error) {
    showToast(error.message, true);
    setFormFeedback("admin-discipline-feedback", error.message, true);
  }
}

async function handleImportRoster(event) {
  event.preventDefault();
  const disciplineId = $("roster-discipline-id").value;
  if (!disciplineId) {
    setFormFeedback("roster-import-feedback", "Selecione uma disciplina antes de importar a turma.", true);
    return;
  }
  try {
    const result = await api(`/admin/disciplines/${disciplineId}/students/import`, {
      method: "POST",
      body: JSON.stringify({
        nomes_texto: $("roster-text").value.trim(),
        arquivo_referencia: $("roster-arquivo").value.trim(),
      }),
    });
    applyRosterImportResult(result, "Turma importada com sucesso.");
    await renderAdmin();
    await selectDiscipline(disciplineId);
  } catch (error) {
    showToast(error.message, true);
    setFormFeedback("roster-import-feedback", error.message, true);
  }
}

async function handleImportRosterPdf() {
  const disciplineId = $("roster-discipline-id").value;
  const file = $("roster-pdf-file").files?.[0];
  const button = $("roster-import-pdf-btn");
  if (!disciplineId) {
    setFormFeedback("roster-import-feedback", "Selecione uma disciplina antes de importar a turma.", true);
    return;
  }
  if (!file) {
    setFormFeedback("roster-import-feedback", "Escolha um PDF da turma para importar.", true);
    return;
  }

  const formData = new FormData();
  formData.append("arquivo", file);

  try {
    button.disabled = true;
    button.textContent = "Importando PDF...";
    setFormFeedback("roster-import-feedback", "Lendo o PDF da turma. Na primeira vez isso pode levar alguns segundos.");
    const headers = {};
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const response = await fetch(`${API_BASE}/admin/disciplines/${disciplineId}/students/import-pdf`, {
      method: "POST",
      headers,
      body: formData,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: "Falha ao importar o PDF." }));
      throw new Error(normalizeMessage(body.detail || body) || "Falha ao importar o PDF.");
    }
    const result = await response.json();
    $("roster-arquivo").value = file.name;
    applyRosterImportResult(result, "Turma importada automaticamente do PDF.");
    $("roster-pdf-file").value = "";
    await renderAdmin();
    await selectDiscipline(disciplineId);
  } catch (error) {
    const message = error?.message === "Failed to fetch"
      ? "A API não respondeu durante a leitura do PDF. Tente novamente com a API ligada, ou use a importação por texto."
      : error.message;
    showToast(message, true);
    setFormFeedback("roster-import-feedback", message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Importar PDF";
  }
}

function editDiscipline(disciplineId) {
  const item = state.disciplines.find((discipline) => discipline.id === Number(disciplineId));
  if (!item) return;
  $("admin-discipline-form").dataset.editingId = String(item.id);
  $("acad-professor").value = item.professor_nome;
  $("acad-ano").value = item.ano;
  $("acad-nome").value = item.nome;
  $("acad-codigo").value = item.codigo;
  $("acad-horario").value = item.horario;
  $("acad-turma").value = item.turma_nome;
  $("acad-arquivo").value = item.arquivo_referencia || "";
  $("acad-cancel-edit").classList.remove("hidden");
  setAdminTab("academic");
  showToast(`Turma ${item.turma_nome} carregada para edição.`);
}

async function deleteDiscipline(disciplineId) {
  const item = state.disciplines.find((discipline) => discipline.id === Number(disciplineId));
  if (!item) return;
  if (!window.confirm(`Deseja excluir a turma ${item.turma_nome} de ${item.nome}?`)) return;
  try {
    await api(`/admin/disciplines/${disciplineId}`, { method: "DELETE" });
    if (state.currentDisciplineId === Number(disciplineId)) {
      state.currentDisciplineId = null;
      $("selected-discipline-title").textContent = "Escolha uma disciplina para importar e usar os alunos";
      state.rosterStudents = [];
    }
    showToast("Turma excluída.");
    await renderAdmin();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function handleCreateEvaluation(event) {
  event.preventDefault();
  try {
    const email = $("eval-email").value.trim();
    const result = await api("/admin/evaluations", {
      method: "POST",
      body: JSON.stringify({
        email,
        roster_student_id: parseNumber($("eval-roster").value),
        nome: $("eval-nome").value.trim(),
        idade: parseNumber($("eval-idade").value),
        sexo: $("eval-sexo").value || null,
        altura_cm: parseNumber($("eval-altura").value),
        tipo_avaliacao: $("eval-tipo").value,
        peso: Number($("eval-peso").value),
        bf: parseNumber($("eval-bf").value),
        agua: parseNumber($("eval-agua").value),
        massa_muscular: parseNumber($("eval-musculo").value),
        bmr: parseNumber($("eval-bmr").value),
        idade_metabolica: parseNumber($("eval-idade-met").value),
        massa_ossea: parseNumber($("eval-massa-ossea").value),
        vo2: parseNumber($("eval-vo2").value),
        cooper: parseNumber($("eval-cooper").value),
        pressao_sist: parseNumber($("eval-sist").value),
        pressao_diast: parseNumber($("eval-diast").value),
        flexibilidade: parseNumber($("eval-flexibilidade").value),
        abd: parseNumber($("eval-abd").value),
        flexao: parseNumber($("eval-flexao").value),
        fc_rep: parseNumber($("eval-fc-rep").value),
        fc_pos: parseNumber($("eval-fc-pos").value),
        fc_rec_5: parseNumber($("eval-fc-rec").value),
        observacoes: $("eval-observacoes").value.trim(),
        fonte: "manual",
      }),
    });
    event.target.reset();
    $("eval-tipo").value = "completa";
    $("eval-sexo").value = "";
    $("eval-discipline").value = state.currentDisciplineId ? String(state.currentDisciplineId) : "";
    updateEvaluationFormVisibility();
    showToast("Avaliação salva com sucesso.");
    setFormFeedback(
      "admin-eval-feedback",
      `Avaliação salva para ${result.payload.email || email || "o usuário"} com score ${result.resultado.score_ia.toFixed(1)}.`
    );
    await renderAdmin();
  } catch (error) {
    showToast(error.message, true);
    setFormFeedback("admin-eval-feedback", error.message, true);
  }
}

async function handleAppointment(event) {
  event.preventDefault();
  try {
    const response = await api("/appointments", {
      method: "POST",
      body: JSON.stringify({
        nome: $("appt-name").value.trim(),
        email: $("appt-email").value.trim(),
        telefone: $("appt-phone").value.trim(),
        data_agendada: $("appt-date").value,
        horario_agendado: $("appt-time").value,
        observacoes: $("appt-notes").value.trim(),
      }),
    });
    event.target.reset();
    populateAppointmentDates();
    if (response.email_status === "enviado") {
      showToast("Agendamento realizado e e-mails enviados com sucesso.");
      setFormFeedback("appointment-feedback", "Agendamento confirmado e e-mail enviado.");
    } else if (response.email_status === "processando_envio") {
      showToast("Agendamento realizado. A confirmação por e-mail será enviada em instantes.");
      setFormFeedback("appointment-feedback", "Agendamento confirmado. O e-mail está sendo enviado.");
    } else if (response.email_status === "pendente_configuração") {
      showToast("Agendamento salvo. Falta apenas configurar o SMTP para o envio automático de e-mails.", true);
      setFormFeedback("appointment-feedback", "Agendamento salvo. O e-mail automático ainda depende da configuração SMTP.", true);
    } else {
      showToast("Agendamento salvo, mas houve falha no envio do e-mail.", true);
      setFormFeedback("appointment-feedback", "Agendamento salvo, mas houve falha no envio do e-mail.", true);
    }
  } catch (error) {
    showToast(error.message, true);
    setFormFeedback("appointment-feedback", error.message, true);
  }
}

async function loadWellnessNews() {
  try {
    const news = await api("/wellness/news");
    if (!news.length) {
      $("news-list").innerHTML = "<p class='muted'>Não foi possível carregar notícias no momento.</p>";
      return;
    }
    $("news-list").innerHTML = news.map((item) => `
      <article class="news-item">
        <strong>${item.title}</strong>
        <p class="news-meta">${item.source} · ${item.published_at}</p>
        <a href="${item.link}" target="_blank" rel="noopener noreferrer">Ler notícia</a>
      </article>
    `).join("");
  } catch (error) {
    $("news-list").innerHTML = "<p class='muted'>Não foi possível carregar notícias no momento.</p>";
  }
}

function renderDashboard(data) {
  const user = data.usuario;
  const latest = data.ultima_avaliacao;
  state.currentDashboardUserId = user.id;
  state.currentDashboardData = data;
  clearFormFeedback("results-email-feedback");

  $("user-greeting").textContent = latest ? `Resultado de ${user.nome}` : `Painel de ${user.nome}`;
  $("user-name").textContent = user.nome;
  $("user-meta").textContent = `${user.email} | ${user.idade ?? "-"} anos | ${user.sexo ?? "-"} | ${user.altura_cm ?? "-"} cm`;
  $("user-created").textContent = `Cadastro em ${formatDate(user.created_at)}`;
  $("send-latest-email-btn").disabled = !latest;
  $("send-all-email-btn").disabled = !data.historico.length;

  renderResources();

  if (!latest) {
    $("score-value").textContent = "--";
    $("score-objective").textContent = "Ainda não existe avaliação cadastrada para este usuário.";
    $("imc-value").textContent = "--";
    $("imc-class").textContent = "-";
    $("vo2-value").textContent = "--";
    $("vo2-class").textContent = "-";
    $("bf-value").textContent = "--";
    $("bf-class").textContent = "-";
    $("pressao-value").textContent = "--";
    $("pressao-class").textContent = "-";
    $("bio-peso-value").textContent = "--";
    $("bio-bf-value").textContent = "--";
    $("bio-agua-value").textContent = "--";
    $("bio-muscle-value").textContent = "--";
    $("bio-bmr-value").textContent = "--";
    $("bio-metabolic-age-value").textContent = "--";
    $("bio-bone-value").textContent = "--";
    createListItems("pontos-fortes", ["Sem avaliação ainda."]);
    createListItems("pontos-fracos", ["Assim que um teste for salvo, a leitura da IA aparecerá aqui."]);
    createListItems("recomendacoes", ["Cadastre uma avaliação pelo painel do administrador."]);
    createListItems("nutrition-list", ["A alimentação personalizada aparecerá depois da primeira avaliação."]);
    renderWeeklyPlan([], user.id);
    renderWaterTracker(0, user.id);
    $("history-list").innerHTML = "<p class='muted'>Sem histórico registrado.</p>";
    $("news-list").innerHTML = "<p class='muted'>As notícias aparecerão aqui quando o painel carregar completamente.</p>";
    setMetricBar("chart-imc-fill", "chart-imc-label", 0, 40);
    setMetricBar("chart-vo2-fill", "chart-vo2-label", 0, 60);
    setMetricBar("chart-bf-fill", "chart-bf-label", 0, 40, "%");
    setMetricBar("chart-score-fill", "chart-score-label", 0, 100);
    return;
  }

  const result = latest.resultado;
  $("score-value").textContent = result.score_ia.toFixed(1);
  $("score-objective").textContent = result.objetivo_sugerido;
  $("imc-value").textContent = result.imc.toFixed(1);
  $("imc-class").textContent = result.imc_class;
  $("vo2-value").textContent = result.vo2_est != null ? result.vo2_est.toFixed(1) : "--";
  $("vo2-class").textContent = result.vo2_class;
  $("bf-value").textContent = latest.payload.bf != null ? `${latest.payload.bf.toFixed(1)}%` : "--";
  $("bf-class").textContent = result.bf_class;
  $("pressao-value").textContent = latest.payload.pressao_sist && latest.payload.pressao_diast
    ? `${latest.payload.pressao_sist}/${latest.payload.pressao_diast}`
    : "--";
  $("pressao-class").textContent = result.pressao_class;
  $("bio-peso-value").textContent = formatMetricValue(latest.payload.peso, " kg");
  $("bio-bf-value").textContent = formatMetricValue(latest.payload.bf, "%");
  $("bio-agua-value").textContent = formatMetricValue(latest.payload.agua, "%");
  $("bio-muscle-value").textContent = formatMetricValue(latest.payload.massa_muscular, "%");
  $("bio-bmr-value").textContent = formatMetricValue(latest.payload.bmr, " kcal", 0);
  $("bio-metabolic-age-value").textContent = formatMetricValue(latest.payload.idade_metabolica, " anos", 0);
  $("bio-bone-value").textContent = formatMetricValue(latest.payload.massa_ossea, " kg");

  createListItems("pontos-fortes", result.pontos_fortes);
  createListItems("pontos-fracos", result.pontos_fracos);
  createListItems("recomendacoes", result.recomendacoes);
  createListItems("nutrition-list", result.nutrition_tips || []);
  renderWeeklyPlan(result.weekly_plan || [], user.id);
  renderWaterTracker(result.daily_water_liters || 0, user.id);

  setMetricBar("chart-imc-fill", "chart-imc-label", result.imc, 40);
  setMetricBar("chart-vo2-fill", "chart-vo2-label", result.vo2_est, 60);
  setMetricBar("chart-bf-fill", "chart-bf-label", latest.payload.bf, 40, "%");
  setMetricBar("chart-score-fill", "chart-score-label", result.score_ia, 100);

  $("history-list").innerHTML = data.historico.map((item) => `
    <div class="history-item">
      <div>
        <strong>${formatDate(item.created_at)}</strong>
        <div class="muted">${item.tipo_avaliacao} · fonte ${item.fonte}</div>
      </div>
      <div class="news-meta">Score ${item.resultado.score_ia.toFixed(1)}</div>
      <div class="muted">Objetivo: ${item.resultado.objetivo_sugerido}</div>
    </div>
  `).join("");
}

function toggleWeeklyItem(index) {
  const userId = state.currentDashboardUserId;
  const latest = state.currentDashboardData?.ultima_avaliacao;
  const plan = latest?.resultado?.weekly_plan || [];
  if (!userId || !plan.length) return;
  const checks = getWeeklyTracker(userId, plan);
  checks[index] = !checks[index];
  saveWeeklyTracker(userId, checks);
  renderWeeklyPlan(plan, userId);
}

function handleWaterAdd() {
  const userId = state.currentDashboardUserId;
  const target = state.currentDashboardData?.ultima_avaliacao?.resultado?.daily_water_liters || 0;
  const amount = parseNumber($("water-amount-input").value);
  if (!userId || !target) {
    showToast("A meta de água aparece depois da primeira avaliação.", true);
    return;
  }
  if (amount == null || amount <= 0) {
    showToast("Informe uma quantidade válida de água em litros.", true);
    return;
  }
  const current = getWaterTracker(userId).total;
  saveWaterTracker(userId, Number((current + amount).toFixed(2)));
  $("water-amount-input").value = "";
  renderWaterTracker(target, userId);
  showToast("Hidratação registrada.");
}

function handleWaterReset() {
  const userId = state.currentDashboardUserId;
  const target = state.currentDashboardData?.ultima_avaliacao?.resultado?.daily_water_liters || 0;
  if (!userId) return;
  saveWaterTracker(userId, 0);
  $("water-amount-input").value = "";
  renderWaterTracker(target, userId);
}

async function cancelAppointment(appointmentId) {
  const item = state.appointments.find((appointment) => String(appointment.id) === String(appointmentId));
  if (!item) return;
  if (!window.confirm(`Deseja desmarcar o agendamento de ${item.nome} em ${item.data_agendada} às ${item.horario_agendado}?`)) {
    return;
  }
  try {
    const response = await api(`/admin/appointments/${appointmentId}`, { method: "DELETE" });
    showToast(response.message, response.email_status !== "enviado");
    await renderAdmin();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function handleSendResultsEmail(scope) {
  const userId = state.currentDashboardUserId;
  if (!userId) {
    showToast("Nenhum usuário selecionado para enviar resultados.", true);
    return;
  }
  clearFormFeedback("results-email-feedback");
  const path = state.auth?.role === "admin"
    ? `/users/${userId}/results/send-email`
    : "/me/results/send-email";
  try {
    const response = await api(path, {
      method: "POST",
      body: JSON.stringify({ scope }),
    });
    const isError = response.status !== "enviado";
    setFormFeedback("results-email-feedback", response.message, isError);
    showToast(response.message, isError);
  } catch (error) {
    setFormFeedback("results-email-feedback", error.message, true);
    showToast(error.message, true);
  }
}

async function loadMyDashboard() {
  const data = await api("/me/dashboard");
  renderDashboard(data);
  await loadWellnessNews();
}

async function loadDashboardAsAdmin(userId) {
  const data = await api(`/users/${userId}/dashboard`);
  renderDashboard(data);
  await loadWellnessNews();
  $("back-admin-btn").classList.remove("hidden");
  showView("user-view");
}

async function bootstrapAuthenticated() {
  if (!state.auth?.token) {
    showView("public-view");
    return;
  }

  if (state.auth.role === "admin") {
    $("back-admin-btn").classList.add("hidden");
    await renderAdmin();
    showView("admin-view");
    return;
  }

  $("back-admin-btn").classList.add("hidden");
  await loadMyDashboard();
  showView("user-view");
}

async function deleteUser(userId) {
  if (!window.confirm("Deseja realmente excluir este usuário e o histórico dele?")) return;
  try {
    await api(`/admin/users/${userId}`, { method: "DELETE" });
    showToast("Usuário removido.");
    await renderAdmin();
  } catch (error) {
    showToast(error.message, true);
  }
}

function bindEvents() {
  $("open-login-btn").addEventListener("click", () => showView("login-view"));
  $("header-login-btn").addEventListener("click", () => showView("login-view"));
  $("back-home-btn").addEventListener("click", () => showView("public-view"));
  $("login-form").addEventListener("submit", handleLogin);
  $("forgot-password-btn").addEventListener("click", () => {
    $("forgot-password-form").classList.toggle("hidden");
    $("forgot-email").value = $("login-email").value.trim();
    clearFormFeedback("password-recovery-feedback");
  });
  $("forgot-password-form").addEventListener("submit", handleForgotPassword);
  $("reset-password-form").addEventListener("submit", handleResetPassword);
  $("appointment-form").addEventListener("submit", handleAppointment);
  $("appt-date").addEventListener("change", populateAppointmentTimes);
  $("admin-main-tab-btn").addEventListener("click", () => setAdminTab("main"));
  $("admin-academic-tab-btn").addEventListener("click", () => setAdminTab("academic"));
  $("admin-user-form").addEventListener("submit", handleCreateUser);
  $("admin-discipline-form").addEventListener("submit", handleCreateDiscipline);
  $("calendar-month-filter").addEventListener("change", (event) => {
    state.calendarMonth = event.target.value;
    renderAppointmentCalendar(state.appointments);
  });
  $("acad-cancel-edit").addEventListener("click", () => {
    resetDisciplineForm();
    clearFormFeedback("admin-discipline-feedback");
  });
  $("acad-year-filter").addEventListener("change", (event) => {
    state.academicYearFilter = event.target.value;
    renderDisciplineCards();
  });
  $("roster-import-form").addEventListener("submit", handleImportRoster);
  $("roster-import-pdf-btn").addEventListener("click", handleImportRosterPdf);
  $("admin-eval-form").addEventListener("submit", handleCreateEvaluation);
  $("eval-entry-mode").addEventListener("change", updateEvaluationEntryMode);
  $("eval-tipo").addEventListener("change", updateEvaluationFormVisibility);
  $("eval-discipline").addEventListener("change", async (event) => {
    const disciplineId = event.target.value;
    if (!disciplineId) return;
    await selectDiscipline(disciplineId);
  });
  $("eval-roster").addEventListener("change", (event) => {
    if (event.target.value) fillEvaluationFromRoster(event.target.value);
  });
  $("eval-search-input").addEventListener("input", () => {
    clearTimeout(bindEvents.searchTimer);
    bindEvents.searchTimer = setTimeout(runEvaluationSearch, 260);
  });
  $("water-add-btn").addEventListener("click", handleWaterAdd);
  $("water-reset-btn").addEventListener("click", handleWaterReset);
  $("send-latest-email-btn").addEventListener("click", () => handleSendResultsEmail("latest"));
  $("send-all-email-btn").addEventListener("click", () => handleSendResultsEmail("all"));
  $("refresh-admin-btn").addEventListener("click", renderAdmin);
  $("back-admin-btn").addEventListener("click", async () => {
    $("back-admin-btn").classList.add("hidden");
    await renderAdmin();
    showView("admin-view");
  });
  document.querySelectorAll("[data-logout]").forEach((button) => button.addEventListener("click", handleLogout));
  document.querySelectorAll(".carousel-dot").forEach((button) => {
    button.addEventListener("click", () => {
      setCarousel(Number(button.dataset.slide || 0));
      startCarousel();
    });
  });

  document.addEventListener("click", async (event) => {
    const viewBtn = event.target.closest("[data-view-user]");
    if (viewBtn) {
      await loadDashboardAsAdmin(viewBtn.getAttribute("data-view-user"));
      return;
    }
    const deleteBtn = event.target.closest("[data-delete-user]");
    if (deleteBtn) {
      await deleteUser(deleteBtn.getAttribute("data-delete-user"));
      return;
    }
    const cancelAppointmentBtn = event.target.closest("[data-cancel-appointment]");
    if (cancelAppointmentBtn) {
      await cancelAppointment(cancelAppointmentBtn.getAttribute("data-cancel-appointment"));
      return;
    }
    const weeklyBtn = event.target.closest("[data-weekly-toggle]");
    if (weeklyBtn) {
      toggleWeeklyItem(Number(weeklyBtn.getAttribute("data-weekly-toggle")));
      return;
    }
    const disciplineBtn = event.target.closest("[data-select-discipline]");
    if (disciplineBtn) {
      await selectDiscipline(disciplineBtn.getAttribute("data-select-discipline"));
      return;
    }
    const editDisciplineBtn = event.target.closest("[data-edit-discipline]");
    if (editDisciplineBtn) {
      editDiscipline(editDisciplineBtn.getAttribute("data-edit-discipline"));
      return;
    }
    const deleteDisciplineBtn = event.target.closest("[data-delete-discipline]");
    if (deleteDisciplineBtn) {
      await deleteDiscipline(deleteDisciplineBtn.getAttribute("data-delete-discipline"));
      return;
    }
    const rosterBtn = event.target.closest("[data-use-roster]");
    if (rosterBtn) {
      fillEvaluationFromRoster(rosterBtn.getAttribute("data-use-roster"));
    }
  });
}

async function init() {
  bindEvents();
  $("acad-ano").value = new Date().getFullYear();
  setCarousel(0);
  startCarousel();
  populateAppointmentDates();
  updateEvaluationEntryMode();
  updateEvaluationFormVisibility();
  await Promise.all([loadConfig(), loadHealth()]);

  if (state.resetToken) {
    showView("login-view");
    $("login-form").classList.add("hidden");
    $("forgot-password-btn").classList.add("hidden");
    $("reset-password-form").classList.remove("hidden");
    setFormFeedback("password-recovery-feedback", "Digite uma nova senha para concluir a recuperação.");
    return;
  }

  if (state.auth?.token) {
    try {
      await bootstrapAuthenticated();
      return;
    } catch (error) {
      setAuth(null);
      showToast("Sua sessão expirou. Entre novamente.", true);
    }
  }

  showView("public-view");
}

init();
