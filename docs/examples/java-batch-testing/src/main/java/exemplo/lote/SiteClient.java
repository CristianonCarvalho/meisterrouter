package exemplo.lote;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Cliente HTTP do site legado. A URL base vem de fora (construtor): em produção aponta para o site real e, nos
 * testes, para o WireMock em localhost. Essa é a única mudança que o código de produção precisa para ser testável.
 */
public class SiteClient {
    private final String urlBase;
    private final int timeoutConexaoMs;
    private final int timeoutLeituraMs;

    public SiteClient(String urlBase, int timeoutConexaoMs, int timeoutLeituraMs) {
        this.urlBase = urlBase.endsWith("/") ? urlBase.substring(0, urlBase.length() - 1) : urlBase;
        this.timeoutConexaoMs = timeoutConexaoMs;
        this.timeoutLeituraMs = timeoutLeituraMs;
    }

    public Resposta get(String caminho, String token) {
        return enviar("GET", caminho, token, null);
    }

    public Resposta post(String caminho, String corpoJson) {
        return enviar("POST", caminho, null, corpoJson);
    }

    private Resposta enviar(String metodo, String caminho, String token, String corpo) {
        HttpURLConnection conexao = null;
        try {
            conexao = (HttpURLConnection) new URL(urlBase + caminho).openConnection();
            conexao.setRequestMethod(metodo);
            conexao.setConnectTimeout(timeoutConexaoMs);
            conexao.setReadTimeout(timeoutLeituraMs);
            conexao.setRequestProperty("Accept", "application/json");
            if (token != null) {
                conexao.setRequestProperty("Authorization", "Bearer " + token);
            }
            if (corpo != null) {
                conexao.setDoOutput(true);
                conexao.setRequestProperty("Content-Type", "application/json");
                try (OutputStream saida = conexao.getOutputStream()) {
                    saida.write(corpo.getBytes(StandardCharsets.UTF_8));
                }
            }
            int status = conexao.getResponseCode();
            InputStream fluxo = status >= 400 ? conexao.getErrorStream() : conexao.getInputStream();
            return new Resposta(status, fluxo == null ? "" : ler(fluxo));
        } catch (IOException e) {
            throw new FalhaDeAcesso(metodo + " " + caminho + " falhou: " + e.getMessage(), e);
        } finally {
            if (conexao != null) {
                conexao.disconnect();
            }
        }
    }

    private static String ler(InputStream fluxo) throws IOException {
        try (InputStream entrada = fluxo) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] bloco = new byte[4096];
            int lido;
            while ((lido = entrada.read(bloco)) != -1) {
                buffer.write(bloco, 0, lido);
            }
            return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
