require "pdf-reader"
require "stringio"

module GoogleDocs
  class PdfTextExtractor
    class Error < StandardError; end

    def self.extract(pdf_bytes)
      PDF::Reader.new(StringIO.new(pdf_bytes)).pages.filter_map do |page|
        page.text.strip.presence
      end.join("\n\n")
    rescue PDF::Reader::MalformedPDFError, PDF::Reader::UnsupportedFeatureError => error
      raise Error, "could not extract PDF text: #{error.message}"
    end
  end
end
