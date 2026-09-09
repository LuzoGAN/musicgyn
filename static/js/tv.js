// Modo TV: atualiza "tocando agora" sem piscar a tela (polling da API).
(function () {
  var root = document.getElementById('tv');
  if (!root) return;
  var url = root.getAttribute('data-queue-url');
  if (!url) return; // sem fila ativa: mostra tela idle + QR

  function render(queue) {
    if (!queue || !queue.now_playing) { location.reload(); return; }
    var now = queue.now_playing;
    var title = document.getElementById('tv-title');
    // só recarrega se a faixa mudou (evita flicker)
    if (title && title.textContent !== now.title) { location.reload(); return; }
    var skip = document.getElementById('tv-skip');
    if (skip && typeof queue.skip_votes !== 'undefined') {
      skip.textContent = '⏭️ ' + queue.skip_votes + '/' + queue.skip_needed + ' votos p/ pular';
      var fill = document.getElementById('tv-skipfill');
      if (fill) fill.style.width = Math.min(100, 100 * queue.skip_votes / queue.skip_needed) + '%';
    }
  }

  setInterval(function () {
    fetch(url).then(function (r) { return r.json(); }).then(render).catch(function () {});
  }, 10000);
})();
