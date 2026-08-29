import React from 'react';
import {Composition} from 'remotion';
import {MemoryHanddrawVideo} from './memory';
import {InfographicVideo} from './video';
import type {InfographicVideoProps, MemoryHanddrawVideoProps} from './types';

const defaults: InfographicVideoProps = {
  fps: 30,
  width: 1920,
  height: 1080,
  totalDurationMs: 3000,
  totalDurationFrames: 90,
  style: '粗线扁平国风卡通',
  pages: [],
};

const memoryDefaults: MemoryHanddrawVideoProps = {
  compositionId: 'MemoryHanddraw',
  fps: 30,
  width: 1920,
  height: 1080,
  totalDurationMs: 3000,
  totalDurationFrames: 90,
  paperColor: '#f7f2e8',
  scenes: [],
};

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="DynamicInfographic"
      component={InfographicVideo}
      width={1920}
      height={1080}
      fps={30}
      durationInFrames={90}
      defaultProps={defaults}
      calculateMetadata={({props}) => ({
        width: props.width,
        height: props.height,
        fps: props.fps,
        durationInFrames: Math.max(1, props.totalDurationFrames),
        props,
        defaultCodec: 'h264',
        defaultPixelFormat: 'yuv420p',
      })}
    />
    <Composition
      id="MemoryHanddraw"
      component={MemoryHanddrawVideo}
      width={1920}
      height={1080}
      fps={30}
      durationInFrames={90}
      defaultProps={memoryDefaults}
      calculateMetadata={({props}) => ({
        width: props.width,
        height: props.height,
        fps: props.fps,
        durationInFrames: Math.max(1, props.totalDurationFrames),
        props,
        defaultCodec: 'h264',
        defaultPixelFormat: 'yuv420p',
      })}
    />
  </>
);
