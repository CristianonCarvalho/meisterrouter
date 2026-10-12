package exemplo.lote;

import java.util.ArrayList;
import java.util.List;

public final class Resumo {
    private int paginas;
    private int gravados;
    private final List<String> rejeitados = new ArrayList<String>();

    void paginaLida() { paginas++; }
    void gravado() { gravados++; }
    void rejeitado(String motivo) { rejeitados.add(motivo); }

    public int getPaginas() { return paginas; }
    public int getGravados() { return gravados; }
    public List<String> getRejeitados() { return rejeitados; }
}
