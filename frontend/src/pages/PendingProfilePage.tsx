import { Button, Card, Result, Space, Typography } from 'antd';
import { useAuth } from '../context/AuthContext';

export function PendingProfilePage() {
  const { user, logout } = useAuth();
  return <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
    <Card style={{ maxWidth: 640, width: '100%' }}>
      <Result status="info" title="Hồ sơ nhân viên đang chờ hoàn thiện"
        subTitle="Đăng nhập Microsoft thành công. Hồ sơ đã được tạo tự động; HR cần bổ sung phòng ban, chức danh và ngày vào làm trước khi mở các chức năng nhân sự."
        extra={<Space wrap>
          <Button type="primary" onClick={() => window.location.reload()}>Kiểm tra lại trạng thái</Button>
          <Button onClick={logout}>Đăng xuất</Button>
        </Space>} />
      <Typography.Paragraph style={{ textAlign: 'center' }}>{user?.login_email}</Typography.Paragraph>
    </Card>
  </div>;
}
