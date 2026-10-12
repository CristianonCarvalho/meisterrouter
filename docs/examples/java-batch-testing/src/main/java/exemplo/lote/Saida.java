package exemplo.lote;

/** Para onde vai o resultado do lote. No dry-run usa-se {@link #DESCARTAR}: tudo é processado, nada é gravado. */
public interface Saida {
    void gravar(String linha);

    Saida DESCARTAR = new Saida() {
        @Override
        public void gravar(String linha) {
            // dry-run: não grava nada
        }
    };
}
