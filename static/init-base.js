/* Sync, first external script: set <base> from this file's URL (works when location.pathname is /). */
(function () {
  var sc = document.currentScript;
  if (!sc || !sc.src) return;
  var u = new URL(sc.src);
  var path = u.pathname.replace(/\/static\/init-base\.js(\?.*)?$/i, "");
  if (!path.endsWith("/")) path += "/";
  var b = document.createElement("base");
  b.href = u.origin + path;
  var charset = document.querySelector("meta[charset]");
  document.head.insertBefore(b, charset && charset.nextSibling ? charset.nextSibling : document.head.firstChild);
})();
