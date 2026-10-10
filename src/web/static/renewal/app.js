const api = "/api/v1/public/renewal";
const $ = (selector) => document.querySelector(selector);
const lookupCard = $("#lookup-card");
const offersCard = $("#offers-card");
const paymentCard = $("#payment-card");
const errorBox = $("#error");
const payButton = $("#pay-button");
let token = sessionStorage.getItem("renewalToken");
let selected = null;
let pollTimer = null;

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function clearError() { errorBox.classList.add("hidden"); }

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers["X-Renewal-Token"] = token;
  const response = await fetch(`${api}${path}`, { ...options, headers });
  let data = {};
  try { data = await response.json(); } catch (_) { /* empty response */ }
  if (!response.ok) throw new Error(data.detail || "Не удалось выполнить запрос. Попробуйте ещё раз.");
  return data;
}

function formatDays(days) {
  if (days % 30 === 0) {
    const months = days / 30;
    const ending = months === 1 ? "месяц" : months < 5 ? "месяца" : "месяцев";
    return `${months} ${ending}`;
  }
  return `${days} дней`;
}

function statusLabel(status) {
  return { ACTIVE: "Активна", EXPIRED: "Истекла", DISABLED: "Отключена", LIMITED: "Ограничена" }[status] || status;
}

async function loadOffers(session) {
  const data = await request("/offers");
  $("#plan-name").textContent = data.plan_name;
  $("#subscription-status").textContent = statusLabel(session.subscription_status);
  $("#expiry-text").textContent = `Действует до ${new Intl.DateTimeFormat("ru-RU", { dateStyle: "long" }).format(new Date(session.expire_at))}`;
  const list = $("#offers");
  list.replaceChildren();
  data.offers.forEach((offer) => offer.prices.forEach((price) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "offer";
    const details = document.createElement("span");
    const duration = document.createElement("span");
    const gateway = document.createElement("span");
    const amount = document.createElement("span");
    duration.className = "offer-duration";
    gateway.className = "offer-gateway";
    amount.className = "offer-price";
    duration.textContent = formatDays(offer.duration_days);
    gateway.textContent = price.gateway_name;
    amount.textContent = `${price.amount} ${price.currency}`;
    details.append(duration, gateway);
    button.append(details, amount);
    button.addEventListener("click", () => {
      document.querySelectorAll(".offer").forEach((item) => item.classList.remove("selected"));
      button.classList.add("selected");
      selected = { duration_days: offer.duration_days, gateway_type: price.gateway_type };
      payButton.disabled = false;
    });
    list.append(button);
  }));
  if (!data.offers.length) showError("Для этой подписки сейчас нет доступных вариантов продления.");
  lookupCard.classList.add("hidden");
  paymentCard.classList.add("hidden");
  offersCard.classList.remove("hidden");
}

$("#lookup-form").addEventListener("submit", async (event) => {
  event.preventDefault(); clearError();
  const submit = event.currentTarget.querySelector("button[type=submit]");
  submit.disabled = true; submit.textContent = "Ищем…";
  try {
    const session = await request("/resolve", {
      method: "POST",
      body: JSON.stringify({ subscription_url: $("#subscription-url").value.trim() }),
    });
    token = session.token;
    sessionStorage.setItem("renewalToken", token);
    sessionStorage.setItem("renewalSession", JSON.stringify(session));
    await loadOffers(session);
  } catch (error) { showError(error.message); }
  finally { submit.disabled = false; submit.textContent = "Найти подписку"; }
});

payButton.addEventListener("click", async () => {
  if (!selected) return;
  clearError(); payButton.disabled = true; payButton.textContent = "Создаём платёж…";
  const paymentWindow = window.open("about:blank", "_blank");
  try {
    const idempotencyKey = crypto.randomUUID();
    const payment = await request("/payments", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(selected),
    });
    showPayment(payment, paymentWindow);
  } catch (error) {
    if (paymentWindow) paymentWindow.close();
    showError(error.message); payButton.disabled = false;
  }
  finally { payButton.textContent = "Перейти к оплате"; }
});

function showPayment(payment, paymentWindow) {
  offersCard.classList.add("hidden");
  paymentCard.classList.remove("hidden");
  const link = $("#payment-link");
  if (payment.payment_url) {
    link.href = payment.payment_url;
    link.classList.remove("hidden");
    if (paymentWindow) paymentWindow.location.replace(payment.payment_url);
  } else {
    if (paymentWindow) paymentWindow.close();
    link.classList.add("hidden");
  }
  if (payment.status === "COMPLETED") return markCompleted();
  pollPayment(payment.payment_id);
}

function pollPayment(paymentId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const payment = await request(`/payments/${paymentId}`);
      if (payment.status === "COMPLETED") markCompleted();
      if (["FAILED", "REFUNDED"].includes(payment.status)) markFailed();
    } catch (_) { /* transient polling errors are retried */ }
  }, 3000);
}

function markCompleted() {
  clearInterval(pollTimer);
  $("#payment-spinner").classList.add("hidden");
  $("#success-mark").classList.remove("hidden");
  $("#payment-title").textContent = "Подписка продлена";
  $("#payment-copy").textContent = "Оплата подтверждена. Бот и панель уже получили обновление.";
  $("#payment-link").classList.add("hidden");
}

function markFailed() {
  clearInterval(pollTimer);
  $("#payment-spinner").classList.add("hidden");
  $("#payment-title").textContent = "Платёж не завершён";
  $("#payment-copy").textContent = "Можно вернуться и создать новый платёж.";
}

$("#change-link").addEventListener("click", () => {
  token = null; selected = null;
  sessionStorage.removeItem("renewalToken");
  sessionStorage.removeItem("renewalSession");
  clearError();
  offersCard.classList.add("hidden"); lookupCard.classList.remove("hidden");
});
$("#back-to-offers").addEventListener("click", () => {
  clearInterval(pollTimer); paymentCard.classList.add("hidden"); offersCard.classList.remove("hidden");
});

const help = $("#help-dialog");
$("#open-help").addEventListener("click", () => help.showModal());
$("#close-help").addEventListener("click", () => help.close());
help.addEventListener("click", (event) => { if (event.target === help) help.close(); });

if (token) {
  try {
    const session = JSON.parse(sessionStorage.getItem("renewalSession"));
    if (session) loadOffers(session).catch(() => {
      sessionStorage.removeItem("renewalToken");
      sessionStorage.removeItem("renewalSession");
    });
  } catch (_) {
    sessionStorage.removeItem("renewalToken");
    sessionStorage.removeItem("renewalSession");
  }
}
