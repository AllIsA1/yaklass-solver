/* Выполняется в самой странице (world MAIN). Пока расширение само выполняет работу в этой вкладке (контент-скрипт
   ставит атрибут data-yk-auto), нативное confirm() сайта принимается автоматически — иначе страница зависла бы
   в ожидании нажатия. В обычной работе пользователя с сайтом confirm() не трогается. */
(function () {
  const original = window.confirm;
  window.confirm = function (...args) {
    return document.documentElement.hasAttribute("data-yk-auto") ? true : original.apply(this, args);
  };
})();
