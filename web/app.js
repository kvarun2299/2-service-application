const form = document.querySelector("#product-form");
const nameInput = document.querySelector("#name");
const priceInput = document.querySelector("#price");
const descriptionInput = document.querySelector("#description");
const submitButton = document.querySelector("#submit-button");
const formMessage = document.querySelector("#form-message");
const catalogMessage = document.querySelector("#catalog-message");
const productList = document.querySelector("#product-list");
const productCount = document.querySelector("#product-count");
const refreshButton = document.querySelector("#refresh-button");

function showFormMessage(message, isError = false) {
  formMessage.textContent = message;
  formMessage.classList.toggle("error", isError);
}

function formatPrice(value) {
  const amount = Number(value);
  return Number.isFinite(amount)
    ? new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(amount)
    : String(value);
}

async function responseError(response, fallback) {
  try {
    const payload = await response.json();
    return payload.detail || fallback;
  } catch {
    return fallback;
  }
}

function renderProducts(products) {
  productList.replaceChildren();
  productCount.textContent = String(products.length);
  catalogMessage.hidden = products.length > 0;
  catalogMessage.classList.remove("error");
  catalogMessage.textContent = products.length
    ? ""
    : "No products yet. Add your first item to get started.";

  for (const product of products) {
    const item = document.createElement("li");
    item.className = "product-card";

    const avatar = document.createElement("span");
    avatar.className = "product-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = product.name.trim().charAt(0).toUpperCase() || "?";

    const details = document.createElement("div");
    details.className = "product-details";
    const name = document.createElement("p");
    name.className = "product-name";
    name.textContent = product.name;
    const description = document.createElement("p");
    description.className = "product-description";
    description.textContent = product.description || "No description";
    details.append(name, description);

    const price = document.createElement("span");
    price.className = "product-price";
    price.textContent = formatPrice(product.price);

    const remove = document.createElement("button");
    remove.className = "delete-button";
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Delete ${product.name}`);
    remove.addEventListener("click", () => deleteProduct(product.id, remove));

    item.append(avatar, details, price, remove);
    productList.append(item);
  }
}

async function loadProducts() {
  catalogMessage.hidden = false;
  catalogMessage.classList.remove("error");
  catalogMessage.textContent = "Loading products…";
  try {
    const response = await fetch("/api/products", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseError(response, "Products could not be loaded."));
    }
    renderProducts(await response.json());
  } catch (error) {
    productList.replaceChildren();
    productCount.textContent = "0";
    catalogMessage.hidden = false;
    catalogMessage.classList.add("error");
    catalogMessage.textContent =
      error instanceof TypeError
        ? "Could not reach the API. Check the connection and try again."
        : error.message;
  }
}

async function deleteProduct(id, button) {
  if (!window.confirm("Delete this product?")) return;
  button.disabled = true;
  try {
    const response = await fetch(`/api/products/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    if (!response.ok && response.status !== 204) {
      throw new Error(
        await responseError(response, "This product could not be deleted."),
      );
    }
    await loadProducts();
  } catch (error) {
    showFormMessage(
      error instanceof TypeError
        ? "Could not reach the API. Try again."
        : error.message,
      true,
    );
    button.disabled = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showFormMessage("");
  submitButton.disabled = true;
  try {
    const response = await fetch("/api/products", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        name: nameInput.value.trim(),
        price: priceInput.value,
        description: descriptionInput.value.trim() || null,
      }),
    });
    if (!response.ok) {
      throw new Error(
        await responseError(response, "The product could not be added."),
      );
    }
    form.reset();
    showFormMessage("Product added.");
    await loadProducts();
  } catch (error) {
    showFormMessage(
      error instanceof TypeError
        ? "Could not reach the API. Check the connection and try again."
        : error.message,
      true,
    );
  } finally {
    submitButton.disabled = false;
  }
});

refreshButton.addEventListener("click", loadProducts);
loadProducts();
