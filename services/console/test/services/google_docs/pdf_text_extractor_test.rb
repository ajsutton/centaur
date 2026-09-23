require "test_helper"

module GoogleDocs
  class PdfTextExtractorTest < ActiveSupport::TestCase
    test "extracts embedded text without OCR" do
      assert_includes PdfTextExtractor.extract(pdf_with_text("Quarterly results")), "Quarterly results"
    end

    test "rejects malformed PDFs" do
      assert_raises(PdfTextExtractor::Error) do
        PdfTextExtractor.extract("not a pdf")
      end
    end

    private

    def pdf_with_text(text)
      objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        nil
      ]
      stream = "BT /F1 12 Tf 72 720 Td (#{text}) Tj ET"
      objects[4] = "<< /Length #{stream.bytesize} >>\nstream\n#{stream}\nendstream"

      pdf = +"%PDF-1.4\n"
      offsets = objects.each_with_index.map do |object, index|
        offset = pdf.bytesize
        pdf << "#{index + 1} 0 obj\n#{object}\nendobj\n"
        offset
      end
      xref_offset = pdf.bytesize
      pdf << "xref\n0 #{objects.length + 1}\n"
      pdf << "0000000000 65535 f \n"
      offsets.each { |offset| pdf << format("%010d 00000 n \n", offset) }
      pdf << "trailer\n<< /Size #{objects.length + 1} /Root 1 0 R >>\n"
      pdf << "startxref\n#{xref_offset}\n%%EOF\n"
    end
  end
end
