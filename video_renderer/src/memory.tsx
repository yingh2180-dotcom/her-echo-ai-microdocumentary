import React from 'react';
import {
  AbsoluteFill,
  Easing,
  Img,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
} from 'remotion';

import type {MemoryHanddrawScene, MemoryHanddrawVideoProps} from './types';

const clamp = {
  extrapolateLeft: 'clamp' as const,
  extrapolateRight: 'clamp' as const,
};

export const getMemoryWipeProgress = (frame: number, durationInFrames: number) => {
  const normalized = frame / Math.max(1, durationInFrames - 1);
  const line = interpolate(normalized, [0.1, 0.38], [0, 100], {
    ...clamp,
    easing: Easing.inOut(Easing.quad),
  });
  const color = interpolate(normalized, [0.43, 0.86], [0, 100], clamp);

  return {line, color};
};

const resolveImage = (path: string) => {
  if (/^(?:https?:|data:|blob:)/i.test(path)) {
    return path;
  }
  return staticFile(path.replace(/^\/+/, ''));
};

const MemoryScene: React.FC<{
  scene: MemoryHanddrawScene;
  paperColor: string;
}> = ({scene, paperColor}) => {
  const frame = useCurrentFrame();
  const durationInFrames = Math.max(1, scene.endFrame - scene.startFrame);
  const {line, color} = getMemoryWipeProgress(frame, durationInFrames);
  const opacity = interpolate(
    frame,
    [0, Math.max(1, Math.round(durationInFrames * 0.03))],
    [0, 1],
    clamp,
  );
  const sharedImageStyle: React.CSSProperties = {
    position: 'absolute',
    inset: 0,
    width: '100%',
    height: '100%',
    objectFit: 'contain',
  };

  return (
    <AbsoluteFill style={{backgroundColor: paperColor, opacity}}>
      <Img
        src={resolveImage(scene.lineImage)}
        style={{...sharedImageStyle, clipPath: `inset(0 ${100 - line}% 0 0)`}}
      />
      <Img
        src={resolveImage(scene.colorImage)}
        style={{...sharedImageStyle, clipPath: `inset(0 ${100 - color}% 0 0)`}}
      />
    </AbsoluteFill>
  );
};

export const MemoryHanddrawVideo: React.FC<MemoryHanddrawVideoProps> = ({
  scenes,
  paperColor = '#f7f2e8',
}) => {
  return (
    <AbsoluteFill
      style={{
        backgroundColor: paperColor,
        backgroundImage:
          'radial-gradient(circle at 20% 15%, rgba(111, 82, 52, 0.035), transparent 35%), radial-gradient(circle at 80% 85%, rgba(111, 82, 52, 0.025), transparent 38%)',
      }}
    >
      {scenes.map((scene) => (
        <Sequence
          key={scene.id}
          from={scene.startFrame}
          durationInFrames={Math.max(1, scene.endFrame - scene.startFrame)}
          layout="none"
        >
          <MemoryScene scene={scene} paperColor={paperColor} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
