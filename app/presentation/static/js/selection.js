// Выбор файлов на странице расчётов.
//
// Пагинация подменяет только таблицу и не перезагружает страницу, поэтому
// посчитанная статистика и выбор остаются на месте. Состояние при этом всё
// равно хранится вне подменяемой разметки:
//
//   * режим «весь каталог» — в адресе страницы, наравне с page и order;
//   * отмеченные имена — в sessionStorage.
//
// Имена, отмеченные на других страницах, дополняются скрытыми полями: без них
// расчёт учёл бы только видимую страницу.
//
// Обработчики навешаны на документ, а не на элементы таблицы: сама таблица
// заменяется при каждом переходе, и привязка к ней терялась бы.

(function () {
  "use strict";

  var STORAGE_KEY = "filestats.selection";
  var SCOPE_CHOSEN = "chosen";
  var SCOPE_EVERYTHING = "everything";

  function byRole(role) {
    return document.querySelector("[data-role='" + role + "']");
  }

  function allByRole(role) {
    return Array.prototype.slice.call(
      document.querySelectorAll("[data-role='" + role + "']")
    );
  }

  function rowBoxes() {
    return allByRole("file-checkbox");
  }

  // --- хранилище выбранных имён ---------------------------------------------

  function readSelection() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (error) {
      return [];
    }
  }

  function writeSelection(names) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(names));
    } catch (error) {
      // Приватный режим или переполнение — выбор просто не переживёт переход.
    }
  }

  function selectionSet() {
    var set = Object.create(null);
    readSelection().forEach(function (name) { set[name] = true; });
    return set;
  }

  function storeFromSet(set) {
    writeSelection(Object.keys(set));
  }

  // --- режим ----------------------------------------------------------------

  function isEverything() {
    var scope = byRole("selection-scope");
    return scope !== null && scope.value === SCOPE_EVERYTHING;
  }

  function applyScopeToLinks(scope) {
    // Переходы отрендерены сервером с прежним режимом — после переключения их
    // надо привести в соответствие, иначе следующая страница сбросит его.
    allByRole("page-link").forEach(function (control) {
      var target = control.getAttribute("hx-get");
      if (target) {
        control.setAttribute("hx-get", target.replace(/scope=[^&]*/, "scope=" + scope));
      }
    });
  }

  // --- отрисовка ------------------------------------------------------------

  function syncOffPageInputs(set) {
    var holder = byRole("offpage-names");
    if (!holder) {
      return;
    }

    var onPage = Object.create(null);
    rowBoxes().forEach(function (box) { onPage[box.value] = true; });

    holder.innerHTML = "";
    if (isEverything()) {
      return;
    }

    Object.keys(set).forEach(function (name) {
      if (onPage[name]) {
        return;
      }
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "names";
      hidden.value = name;
      hidden.setAttribute("data-role", "offpage-name");
      holder.appendChild(hidden);
    });
  }

  function refresh() {
    var set = selectionSet();
    var everything = isEverything();
    var table = byRole("files-table");
    var counter = byRole("selection-count");

    rowBoxes().forEach(function (box) {
      box.checked = !everything && Boolean(set[box.value]);
      box.disabled = everything;
      var row = box.closest("tr");
      if (row) {
        row.classList.toggle("is-selected", box.checked);
      }
    });

    var selectPageButton = byRole("select-page");
    if (selectPageButton) {
      selectPageButton.disabled = everything;
    }

    if (table) {
      table.classList.toggle("table--locked", everything);
    }

    syncOffPageInputs(set);

    if (counter) {
      var total = Number(counter.dataset.total || 0);
      var chosen = Object.keys(set).length;
      counter.textContent = everything
        ? "Выбрано: весь каталог (" + total + ")"
        : "Выбрано: " + chosen;
      counter.classList.toggle("selection-count--active", everything || chosen > 0);
    }
  }

  // --- действия -------------------------------------------------------------

  function toggleName(name, checked) {
    var set = selectionSet();
    if (checked) {
      set[name] = true;
    } else {
      delete set[name];
    }
    storeFromSet(set);
    refresh();
  }

  function selectPage() {
    if (isEverything()) {
      return;
    }
    var set = selectionSet();
    rowBoxes().forEach(function (box) { set[box.value] = true; });
    storeFromSet(set);
    refresh();
  }

  function clearAll() {
    writeSelection([]);
    refresh();
  }

  function setScope(everything) {
    var scope = everything ? SCOPE_EVERYTHING : SCOPE_CHOSEN;
    byRole("selection-scope").value = scope;

    var label = byRole("select-everything-label");
    if (label) {
      label.classList.toggle("switch--on", everything);
    }

    applyScopeToLinks(scope);
    refresh();
  }

  function handleRowClick(event, table) {
    var row = event.target.closest("tr");
    if (!row || !table.tBodies[0] || !table.tBodies[0].contains(row)) {
      return;
    }

    var box = row.querySelector("[data-role='file-checkbox']");
    if (!box) {
      return;
    }

    // Клик по самому чекбоксу браузер обработает сам — иначе состояние
    // переключилось бы дважды и осталось прежним.
    toggleName(box.value, event.target === box ? box.checked : !box.checked);
  }

  document.addEventListener("click", function (event) {
    var target = event.target;

    if (target.closest("[data-role='select-page']")) {
      selectPage();
      return;
    }

    if (target.closest("[data-role='clear-all']")) {
      clearAll();
      return;
    }

    var table = target.closest("[data-role='files-table']");
    if (table && !isEverything()) {
      handleRowClick(event, table);
    }
  });

  document.addEventListener("change", function (event) {
    if (event.target.matches("[data-role='select-everything']")) {
      setScope(event.target.checked);
    }
  });

  // Таблица заменяется целиком — состояние надо нанести на новую разметку.
  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.target && event.target.id === "file-list") {
      refresh();
    }
  });

  document.addEventListener("DOMContentLoaded", refresh);
})();
