// The selected area is expressed in original-video coordinates. Inference may
// resize the crop, but every point shown to the coach is mapped back here.
export function normalizeRegion(region) {
  if (region === null || region === undefined) return null;
  const keys = ['x', 'y', 'width', 'height'];
  if (typeof region !== 'object' || !keys.every((key) => Number.isFinite(region[key]))) {
    throw new Error('请选择完整的分析区域。');
  }
  const { x, y, width, height } = region;
  const epsilon = 1e-9;
  if (x < -epsilon || y < -epsilon || width < .05 - epsilon || height < .05 - epsilon ||
      x + width > 1 + epsilon || y + height > 1 + epsilon) {
    throw new Error('分析区域须在画面内，宽度和高度至少占画面的 5%。');
  }
  const boundedX = Math.max(0, Math.min(1, x));
  const boundedY = Math.max(0, Math.min(1, y));
  return { x: boundedX, y: boundedY,
    width: Math.min(width, 1 - boundedX), height: Math.min(height, 1 - boundedY) };
}

export function cropRect(region, width, height) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) {
    throw new Error('无法读取视频画面大小。');
  }
  const area = normalizeRegion(region) || { x: 0, y: 0, width: 1, height: 1 };
  const sx = Math.max(0, Math.floor(area.x * width));
  const sy = Math.max(0, Math.floor(area.y * height));
  const ex = Math.min(width, Math.ceil((area.x + area.width) * width));
  const ey = Math.min(height, Math.ceil((area.y + area.height) * height));
  if (ex - sx < 16 || ey - sy < 16) throw new Error('选择的分析区域过小，请扩大范围。');
  return { sx, sy, sw: ex - sx, sh: ey - sy };
}

export function mapLandmark(point, crop, width, height) {
  return { ...point,
    x: (crop.sx + point.x * crop.sw) / width,
    y: (crop.sy + point.y * crop.sh) / height,
    ...(Number.isFinite(point.z) ? { z: point.z * crop.sw / width } : {}),
  };
}
