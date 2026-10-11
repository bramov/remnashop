const api = "/api/v1/public/renewal";
const $ = (selector) => document.querySelector(selector);
const lookupCard = $("#lookup-card");
const offersCard = $("#offers-card");
const paymentCard = $("#payment-card");
const errorBox = $("#error");
const payButton = $("#pay-button");
const lookupForm = $("#lookup-form");
const subscriptionInput = $("#subscription-url");
const lookupButton = lookupForm.querySelector("button[type=submit]");
let token = sessionStorage.getItem("renewalToken");
let selected = null;
let pollTimer = null;

function normalizeSubscriptionUrl(value) {
  try {
    const url = new URL(value.trim());
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch (_) {
    return value.trim();
  }
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function clearError() { errorBox.classList.add("hidden"); }

function errorMessage(detail, status) {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (status === 422) return "Введите корректную HTTPS-ссылку подписки.";
  return "Не удалось выполнить запрос. Попробуйте ещё раз.";
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers["X-Renewal-Token"] = token;
  const response = await fetch(`${api}${path}`, { ...options, headers });
  let data = {};
  try { data = await response.json(); } catch (_) { /* empty response */ }
  if (!response.ok) throw new Error(errorMessage(data.detail, response.status));
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
  const currentSession = {
    ...session,
    plan_name: data.plan_name,
    subscription_status: data.subscription_status,
    expire_at: data.expire_at,
  };
  sessionStorage.setItem("renewalSession", JSON.stringify(currentSession));
  $("#plan-name").textContent = data.plan_name;
  updateSubscriptionMeta(currentSession);
  const list = $("#offers");
  list.replaceChildren();
  data.offers.forEach((offer) => offer.prices.forEach((price) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "offer";
    const details = document.createElement("span");
    const duration = document.createElement("span");
    const gateway = document.createElement("span");
    const priceWrap = document.createElement("span");
    const amount = document.createElement("span");
    const originalAmount = document.createElement("span");
    const discount = document.createElement("span");
    duration.className = "offer-duration";
    gateway.className = "offer-gateway";
    priceWrap.className = "offer-price-wrap";
    amount.className = "offer-price";
    originalAmount.className = "offer-original-price";
    discount.className = "offer-discount";
    duration.textContent = formatDays(offer.duration_days);
    gateway.textContent = price.gateway_name;
    amount.textContent = `${price.amount} ${price.currency}`;
    originalAmount.textContent = `${price.original_amount} ${price.currency}`;
    discount.textContent = `−${price.discount_percent}%`;
    details.append(duration, gateway);
    if (price.discount_percent > 0) priceWrap.append(discount, originalAmount);
    priceWrap.append(amount);
    button.append(details, priceWrap);
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

function updateSubscriptionMeta(session) {
  $("#subscription-status").textContent = statusLabel(session.subscription_status);
  $("#expiry-text").textContent = `Действует до ${new Intl.DateTimeFormat("ru-RU", { dateStyle: "long" }).format(new Date(session.expire_at))}`;
}

function syncLookupButton() {
  lookupButton.disabled = !subscriptionInput.value.trim();
}

subscriptionInput.addEventListener("input", syncLookupButton);
syncLookupButton();

lookupForm.addEventListener("submit", async (event) => {
  event.preventDefault(); clearError();
  if (!subscriptionInput.value.trim()) return;
  lookupButton.disabled = true; lookupButton.textContent = "Ищем…";
  try {
    const normalizedUrl = normalizeSubscriptionUrl(subscriptionInput.value);
    subscriptionInput.value = normalizedUrl;
    const session = await request("/resolve", {
      method: "POST",
      body: JSON.stringify({ subscription_url: normalizedUrl }),
    });
    token = session.token;
    sessionStorage.setItem("renewalToken", token);
    sessionStorage.setItem("renewalSession", JSON.stringify(session));
    await loadOffers(session);
  } catch (error) { showError(error.message); }
  finally { lookupButton.textContent = "Найти подписку"; syncLookupButton(); }
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
  finally { payButton.textContent = "Продлить подписку"; }
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
  if (payment.status === "COMPLETED") return refreshCompletedPayment(payment.payment_id);
  pollPayment(payment.payment_id);
}

async function refreshCompletedPayment(paymentId) {
  try {
    markCompleted(await request(`/payments/${paymentId}`));
  } catch (_) {
    markCompleted();
  }
}

function pollPayment(paymentId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const payment = await request(`/payments/${paymentId}`);
      if (payment.status === "COMPLETED") markCompleted(payment);
      if (["FAILED", "REFUNDED"].includes(payment.status)) markFailed();
    } catch (_) { /* transient polling errors are retried */ }
  }, 3000);
}

function markCompleted(payment = null) {
  clearInterval(pollTimer);
  if (payment?.expire_at) {
    const storedSession = JSON.parse(sessionStorage.getItem("renewalSession") || "{}");
    const currentSession = {
      ...storedSession,
      subscription_status: payment.subscription_status,
      expire_at: payment.expire_at,
    };
    sessionStorage.setItem("renewalSession", JSON.stringify(currentSession));
    updateSubscriptionMeta(currentSession);
  }
  $("#payment-spinner").classList.add("hidden");
  $("#success-mark").classList.remove("hidden");
  $("#payment-title").textContent = "Подписка продлена";
  $("#payment-copy").textContent = "Нажмите кнопку обновления в вашем vpn-клиенте, чтобы снова воспользоваться VPN.";
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

const guideByClient = {
  happ: {
    name: "Happ",
    icon: "./assets/happ-icon.png",
    shot: "./assets/happ-menu.png",
    secondStep: "Выберите «Редактировать».",
    hasCopyStep: true,
  },
  incy: {
    name: "Incy",
    icon: "./assets/incy-icon.png",
    shot: "./assets/incy-menu.png",
    secondStep: "Выберите «Копировать URL».",
    hasCopyStep: false,
  },
};

function showClientGuide(client) {
  const guide = guideByClient[client];
  if (!guide) return;
  $("#guide-client-name").textContent = guide.name;
  $("#guide-icon").src = guide.icon;
  $("#guide-shot").src = guide.shot;
  $("#guide-second-step").textContent = guide.secondStep;
  $("#guide-happ-copy").classList.toggle("hidden", !guide.hasCopyStep);
  $("#client-picker").classList.add("hidden");
  $("#client-guide").classList.remove("hidden");
  help.scrollTop = 0;
}

document.querySelectorAll("[data-client]").forEach((button) => {
  button.addEventListener("click", () => showClientGuide(button.dataset.client));
});
$("#guide-back").addEventListener("click", () => {
  $("#client-guide").classList.add("hidden");
  $("#client-picker").classList.remove("hidden");
});

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
