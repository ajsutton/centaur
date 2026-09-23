require "pdf-reader"
require "timeout"

module GoogleDocs
  class PdfTextExtractor
    class Error < StandardError; end

    MAX_PAGES = 1_000
    MAX_TEXT_CHARS = 5_000_000
    TIMEOUT_SECONDS = 60

    def self.extract(
      path,
      max_pages: MAX_PAGES,
      max_text_chars: MAX_TEXT_CHARS,
      timeout_seconds: TIMEOUT_SECONDS
    )
      Timeout.timeout(timeout_seconds) do
        reader = PDF::Reader.new(path)
        raise Error, "PDF exceeds the #{max_pages}-page indexing limit" if reader.page_count > max_pages

        text = +""
        reader.pages.each do |page|
          page_text = sanitize(page.text).strip
          next if page_text.empty?

          text << "\n\n" unless text.empty?
          if text.length + page_text.length > max_text_chars
            raise Error, "PDF text exceeds the indexing limit"
          end

          text << page_text
        end
        text
      end
    rescue Error
      raise
    rescue Timeout::Error
      raise Error, "PDF text extraction exceeded #{timeout_seconds} seconds"
    rescue StandardError => error
      raise Error, "could not extract PDF text: #{error.class}"
    end

    def self.sanitize(text)
      text.to_s.encode(Encoding::UTF_8, invalid: :replace, undef: :replace, replace: "").delete("\u0000")
    end
    private_class_method :sanitize
  end
end
