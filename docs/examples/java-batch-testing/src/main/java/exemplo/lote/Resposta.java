package exemplo.lote;

public final class Resposta {
    private final int status;
    private final String corpo;

    public Resposta(int status, String corpo) {
        this.status = status;
        this.corpo = corpo;
    }

    public int getStatus() { return status; }
    public String getCorpo() { return corpo; }
}
