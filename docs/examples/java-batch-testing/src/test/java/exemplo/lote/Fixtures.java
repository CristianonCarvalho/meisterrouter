package exemplo.lote;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/** Lê arquivos de src/test/resources. */
final class Fixtures {
    private Fixtures() {}

    static String ler(String caminho) {
        try (InputStream entrada = Fixtures.class.getClassLoader().getResourceAsStream(caminho)) {
            if (entrada == null) {
                throw new IllegalArgumentException("fixture não encontrada: " + caminho);
            }
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] bloco = new byte[4096];
            int lido;
            while ((lido = entrada.read(bloco)) != -1) {
                buffer.write(bloco, 0, lido);
            }
            return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }
}
