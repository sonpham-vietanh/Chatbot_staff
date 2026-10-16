/** Lấy thông báo lỗi hiển thị cho người dùng từ body response lỗi.
 * Backend trả {detail: 'chuỗi tiếng Việt'}; nếu detail là mảng/object (lỗi validation thô) thì
 * hiện chuỗi chung thay vì đổ JSON hay "[object Object]" ra màn hình. */
export function errorMessage(data, status, fallback) {
  const detail = data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail) || (detail && typeof detail === 'object')) return 'Dữ liệu chưa hợp lệ'
  return fallback || (status ? `Lỗi ${status}` : 'Có lỗi xảy ra, vui lòng thử lại.')
}

/** Lỗi mạng của fetch (TypeError "Failed to fetch"...) → câu tiếng Việt. */
export function networkMessage(error) {
  if (error instanceof TypeError) return 'Mất kết nối mạng, vui lòng thử lại'
  return error?.message || 'Có lỗi xảy ra, vui lòng thử lại.'
}
